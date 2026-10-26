"""Cocotb/pyuvm verification environment for the APB slave register block."""

import pyuvm
from pyuvm import *
import cocotb
import os
from pathlib import Path
from cocotb.triggers import Timer , RisingEdge , FallingEdge
import random
from cocotb_coverage.coverage import coverage_section , CoverPoint , CoverCross, coverage_db
#from abc import ABC , abstractmethod
import asyncio


class reset_handler:
    """Mixin interface for components that must discard state on reset."""

    def handle_reset(self):
        pass


class apb_item_base(uvm_sequence_item):
    """Common fields shared by sequence requests and monitored transfers."""

    def __init__(self, name):
        super().__init__(name)
        # APB uses byte addresses; aligned word accesses have addr[1:0] == 0.
        self.addr = 0
        self.data = 0
        # 0 denotes a read and 1 denotes a write.
        self.dir = 0
        self.pstrb = 0xF

    def __str__(self):
        result = f"register_addr : {self.addr : 02x} , data : {self.data : 08x} , pstrb : {self.pstrb : 01x} , direction : {"APB_WRITE" if self.dir == 1 else "APB_READ"}"
        return result

class apb_item_drv(apb_item_base):
    """Sequence item with randomized bus idle delays for the driver."""

    def __init__(self, name):
        super().__init__(name)
        self.pre_item_drv_delay  = 0
        self.post_item_drv_delay = 0

    def randomize(self):
        self.pre_item_drv_delay  = random.randint(0,10)
        self.addr                = random.randint(0,0xFF) & ~0x03
        self.data                = random.randint(0,0xFFFF_FFFF)
        self.dir                 = random.randint(0,1)
        self.pstrb               = random.randint(0, 0xF)
        self.post_item_drv_delay = random.randint(0,10)

    def __str__(self):
        result =  super().__str__() + f" , pre_drv_delay : {self.pre_item_drv_delay} , post_drv_delay : {self.post_item_drv_delay}"
        return result

class apb_item_mon(apb_item_base):
    """Observed transfer plus response, transfer length, and preceding gap."""

    def __init__(self, name):
        super().__init__(name)
        self.response = 0
        self.prev_item_delay = 0
        self.length = 0

    def __str__(self):
        result = super().__str__() + f" , item_response :{"APB_OK" if self.response == 0 else "APB_ERR"} , item lenght :{self.length} , prev_item_delay{self.prev_item_delay} "
        return result

class apb_sequence_random(uvm_sequence):
    """Issue one randomized APB transfer."""

    def __init__(self, name="uvm_sequence"):
        super().__init__(name)
    async def body(self):
        await super().body()
        self.item = apb_item_drv.create("item")
        self.item.randomize()
        await self.start_item(self.item)
        await self.finish_item(self.item)

class apb_sequence_pstrb(uvm_sequence):
    """Exercise all 16 byte-strobe masks against REG_0 and read each result."""

    def __init__(self, name="uvm_sequence"):
        super().__init__(name)

    async def body(self):
        await super().body()

        # Seed the register so every byte-enable combination can verify both
        # the written lanes and the preservation of the other lanes.
        item = apb_item_drv.create("pstrb_seed")
        item.addr = 0x00
        item.dir = 1
        item.data = 0xA5C37E19
        item.pstrb = 0xF
        await self.start_item(item)
        await self.finish_item(item)
        for pstrb_value in range(0x10):
            write_item = apb_item_drv.create(f"pstrb_write_{pstrb_value:X}")
            write_item.addr = 0x00
            write_item.dir = 1
            write_item.data = (0x10203040 + pstrb_value * 0x01010101) & 0xFFFF_FFFF
            write_item.pstrb = pstrb_value
            await self.start_item(write_item)
            await self.finish_item(write_item)

            read_item = apb_item_drv.create(f"pstrb_read_{pstrb_value:X}")
            read_item.addr = 0x00
            read_item.dir = 0
            await self.start_item(read_item)
            await self.finish_item(read_item)

class apb_sequence_all_ones(uvm_sequence):
    """Write all ones to every writable register, then read all 16 slots."""

    def __init__(self, name="uvm_sequence"):
        super().__init__(name)

    async def body(self):
        await super().body()

        for register_index in range(15):
            write_item = apb_item_drv.create(f"all_ones_write_{register_index}")
            write_item.addr = register_index * 4
            write_item.dir = 1
            write_item.data = 0xFFFF_FFFF
            write_item.pstrb = 0xF
            await self.start_item(write_item)
            await self.finish_item(write_item)

        for register_index in range(16):
            read_item = apb_item_drv.create(f"all_ones_read_{register_index}")
            read_item.addr = register_index * 4
            read_item.dir = 0
            await self.start_item(read_item)
            await self.finish_item(read_item)

class apb_sequence_ro(uvm_sequence):
    """Attempt a write to the read-only ID register."""

    def __init__(self, name="uvm_sequence"):
        super().__init__(name)
    async def body(self):
        await super().body()
        self.item = apb_item_drv.create("item")
        self.item.addr = 0x3C
        self.item.dir  = 1
        await self.start_item(self.item)
        await self.finish_item(self.item)

class apb_sequence_illegal(uvm_sequence):
    """Issue one read or write to an address outside the implemented map."""

    def __init__(self, name="uvm_sequence"):
        super().__init__(name)

    async def body(self): 
        await super().body()
        self.tr = apb_item_drv.create("tr")
        self.tr.dir = random.randint(0 , 1)
        self.tr.data = random.randint(0 , 0xFFFF_FFFF)
        self.tr.pstrb = 0xF
        self.tr.addr = random.randint(0x3C + 4 , 0xFF)
        await self.start_item(self.tr)
        await self.finish_item(self.tr)

class apb_driver(uvm_driver , reset_handler):
    """Drive APB setup/access phases and wait for the slave handshake."""

    def __init__(self, name, parent):
        super().__init__(name, parent)
        self.dut = None
        self.drive_transactions_process = None

    async def drive_transaction(self , item):
        self.logger.info(f"item driven ->{item.__str__()}")
        # Idle cycles before setup help exercise transaction spacing.
        for _ in range(item.pre_item_drv_delay):
            await RisingEdge(self.dut.pclk)
        # Setup phase: present the request while PENABLE is low.
        self.dut.psel.value = 1
        self.dut.paddr.value = item.addr
        self.dut.pwrite.value = item.dir
        if item.dir == 1 :
            self.dut.pwdata.value = item.data
        self.dut.pstrb.value  = item.pstrb
        await RisingEdge(self.dut.pclk)
        # Access phase: hold the request until the slave asserts PREADY.
        self.dut.penable.value = 1
        await RisingEdge(self.dut.pclk)
        while self.dut.pready.value != 1:
            await RisingEdge(self.dut.pclk)
        # End the transfer and return the bus to its idle values.
        self.dut.psel.value    = 0
        self.dut.paddr.value   = 0
        self.dut.pstrb.value   = 0
        self.dut.pwrite.value  = 0
        self.dut.pwdata.value  = 0
        self.dut.penable.value = 0
        # Optional idle cycles after completion.
        for _ in range(item.post_item_drv_delay):
            await RisingEdge(self.dut.pclk)

    async def drive_transactions(self):
        """Fetch and complete sequence items until reset cancels this task."""
        while True:
            item =  await self.seq_item_port.get_next_item()
            await self.drive_transaction(item)
            self.seq_item_port.item_done()

    async def run_phase(self):
        await super().run_phase()
        while True:
            await self.wait_reset_end()
            # Keep the transaction loop cancellable when reset interrupts it.
            self.drive_transactions_process =  cocotb.start_soon(self.drive_transactions())
            try:
                await self.drive_transactions_process
            except asyncio.CancelledError:
                if self.drive_transactions_process is not None:
                    raise
    
    async def wait_reset_end(self):
        """Wait for an active-low reset to be released on a rising clock edge."""
        while self.dut.presetn.value == 0 :
            await RisingEdge(self.dut.pclk) 
    
    def handle_reset(self):
        super().handle_reset()   
        if self.drive_transactions_process is not None: 
            # Stop any partially driven transfer and unblock the sequence.
            self.drive_transactions_process.cancel()
            self.drive_transactions_process = None
            self.seq_item_port.item_done()
        # Reset bus outputs to idle values.
        self.dut.psel.value    = 0
        self.dut.paddr.value   = 0
        self.dut.pstrb.value   = 0
        self.dut.pwrite.value  = 0
        self.dut.pwdata.value  = 0
        self.dut.penable.value = 0

class apb_monitor(uvm_monitor , reset_handler):
    """Observe completed APB transfers and publish them to the environment."""

    def __init__(self, name, parent=None):
        super().__init__(name, parent)
        self.dut = None
        self.output_port = uvm_analysis_port("output_port" , self)
        self.port_out_model = uvm_analysis_port("port_out_model" , self)
        self.collect_transactions_process = None
    async def collect_transaction(self , item):
        # Count idle clocks since the preceding observed transfer.
        while self.dut.psel.value != 1 :
            item.prev_item_delay +=1
            await RisingEdge(self.dut.pclk)
        # Capture request fields during setup.
        item.addr = int(self.dut.paddr.value)
        item.dir  = int(self.dut.pwrite.value)
        # Write data is present in the request; read data arrives at completion.
        if item.dir == 1:
            item.data       = int(self.dut.pwdata.value)

        item.pstrb      = int(self.dut.pstrb.value)
        
        # Update the reference model before the completion is scored.
        self.port_out_model.write(item)

        item.length = 1
        await RisingEdge(self.dut.pclk)
        item.length +=1
        while self.dut.pready.value != 1:
            item.length +=1
            await RisingEdge(self.dut.pclk)
           
        # PREADY marks completion; capture response and read data at this point.
        item.response = int(self.dut.pslverr.value)
        if item.dir == 0:
            item.data = int(self.dut.prdata.value)

        # Broadcast the completed transfer to the scoreboard and coverage.
        self.output_port.write(item)

        self.logger.info(f"item monitored -> {item.__str__()}")
        await RisingEdge(self.dut.pclk)

    async def collect_transactions(self):
        """Continuously allocate and collect monitor transactions."""
        while True:
            item = apb_item_mon.create("item")
            await self.collect_transaction(item)

    async def wait_reset_end(self):
        """Keep collection paused while the active-low reset is asserted."""
        while self.dut.presetn.value == 0 :
            await RisingEdge(self.dut.pclk)

    def handle_reset(self):
        if self.collect_transactions_process is not None:
            # Discard an incomplete observation interrupted by reset.
            self.collect_transactions_process.cancel()
            self.collect_transactions_process = None

    async def run_phase(self):
        await super().run_phase()
        while True:
            await self.wait_reset_end()
            self.collect_transactions_process = cocotb.start_soon(self.collect_transactions())
            try:
                await self.collect_transactions_process
            except asyncio.CancelledError:
                if self.collect_transactions_process is not None:
                    raise

class apb_sqr(uvm_sequencer):
    """Standard pyuvm sequencer that arbitrates sequence items for the driver."""

    def __init__(self, name, parent=None):
        super().__init__(name, parent)

class apb_agent(uvm_agent , reset_handler):
    """Bundle the APB sequencer, driver, monitor, and reset coordination."""

    def __init__(self, name, parent=None):
        super().__init__(name, parent)
        self.dut = ConfigDB().get(self , "" , "dut")

    def build_phase(self):
        super().build_phase()
        # The agent is active: sequences drive traffic and the monitor observes it.
        self.driver = apb_driver.create("driver" ,self)
        self.sqr = apb_sqr.create("sqr" , self)
        self.monitor = apb_monitor.create("monitor" ,self)
    
    def connect_phase(self):
        super().connect_phase()
        self.driver.seq_item_port.connect(self.sqr.seq_item_export)
        # Give the bus-facing components access to DUT signals.
        self.driver.dut = self.dut
        self.monitor.dut = self.dut

    async def wait_reset_start(self):
        if self.dut.presetn.value != 0:
            await FallingEdge(self.dut.presetn)

    async def wait_reset_end(self):
        while self.dut.presetn.value == 0 :
            await RisingEdge(self.dut.pclk)

    def handle_reset(self):
        super().handle_reset()
        children = self.get_children()
        for child in children:
            # Delegate reset handling to children that implement the mixin.
            if isinstance(child , reset_handler):
                child.handle_reset()
    
    async def run_phase(self):
        await super().run_phase()
        while True :
            await self.wait_reset_start()
            self.handle_reset()
            await self.wait_reset_end()
class apb_reg_rw(uvm_reg):
    """One 32-bit read/write register in the UVM register model."""

    def __init__(self, name="", n_bits = 32, has_coverage = 0, **kwargs):
        super().__init__(name, n_bits, has_coverage, **kwargs)

    def build(self):
        self.value = uvm_reg_field("value")
        self.value.configure(self , 32 , 0 , "RW" , False , 0 , True , True , False)

class apb_reg_ro(uvm_reg):
    """Read-only ID register model with the RTL's reset value."""

    def __init__(self, name="", n_bits = 32, has_coverage = 0, **kwargs):
        super().__init__(name, n_bits, has_coverage, **kwargs)
        self.ID =  0xA9B00001

    def build(self):
        self.value = uvm_reg_field("value")
        self.value.configure(self , 32 , 0 , "RO" , False , self.ID , True , True , False)
class apb_reg_block(uvm_reg_block):
    """Register map for 15 RW words followed by the read-only ID word."""

    def __init__(self, name = "", has_coverage = uvm_coverage_model_e.UVM_NO_COVERAGE):
        super().__init__(name, has_coverage)
    def build(self):
        self._default_map = self.create_map("apb_map" , 0 , 4 , uvm_endianness_e.UVM_LITTLE_ENDIAN , True)
        # Map each 32-bit RW word at a four-byte aligned address.
        for i in range(15):
            reg = apb_reg_rw(f"REG_{i}")
            reg.configure(self)
            reg.build()
            self._default_map.add_reg(reg , i*4 , "RW")
        # The final mapped word is read-only.
        reg_ro = apb_reg_ro("ID")
        reg_ro.configure(self)
        reg_ro.build()
        self._default_map.add_reg(reg_ro , 0x3C , "RO")

class apb_adapter(uvm_reg_adapter):
    """Translate between UVM register operations and APB sequence items."""
    def __init__(self, name = ""):
        super().__init__(name)
    
    def reg2bus(self, rw):
        tx = apb_item_drv.create("tx")
        #tx.randomize()
        tx.addr = rw.addr
        tx.dir  = 1 if rw.kind == uvm_access_e.UVM_WRITE else 0
        if rw.kind == uvm_access_e.UVM_WRITE:
            tx.data = rw.data
        return tx
    
    
    def bus2reg(self, bus_item, rw):
        rw.kind = uvm_access_e.UVM_WRITE if bus_item.dir == 1 else uvm_access_e.UVM_READ
        rw.addr = bus_item.addr
        rw.data = bus_item.data
        rw.status = uvm_status_e.UVM_IS_OK 


class apb_analysis_imp(uvm_analysis_imp):
    """Analysis implementation that forwards writes to a supplied callback."""

    def __init__(self, name, parent, write_callback):
        super().__init__(name, parent)
        self.write_callback = write_callback

    def write(self, transaction):
        self.write_callback(transaction)


class apb_model(uvm_component , reset_handler):
    """Predict expected bus responses and maintain the register mirror."""

    def __init__(self, name, parent=None):
        super().__init__(name, parent)
        self.input_port           = apb_analysis_imp("input_port", self, self.write)
        self.output_port_response = uvm_analysis_port("output_port_response" , self)
        self.output_port_data     = uvm_analysis_port("output_port_data" , self)
        self.reg_block            = None
    def build_phase(self):
        super().build_phase()
        if self.reg_block == None:
            self.reg_block = apb_reg_block("reg_block")
            self.reg_block.build()
            self.reg_block.lock_model()
            # pre configure the id register
            self.reg_block._default_map.get_reg_by_offset(0x3C).predict(0xA9B00001)
        
    def write(self , tr):
        response = self.get_exp_response(tr)
        # Queue the expected status before the monitor publishes completion.
        self.output_port_response.write(response)

        if response == 0 and tr.dir == 0 :
            reg = self.reg_block._default_map.get_reg_by_offset(tr.addr)
            data_read = reg.get_mirrored_value()
            # Compare successful reads against the current mirrored value.
            self.output_port_data.write(data_read)
        elif response == 0 and tr.dir == 1:
            # Predict only the byte lanes enabled by PSTRB.
            reg = self.reg_block._default_map.get_reg_by_offset(tr.addr)
            current_data = reg.get_mirrored_value()
            byte_mask = 0
            for byte_lane in range(4):
                if tr.pstrb & (1 << byte_lane):
                    byte_mask |= 0xFF << (8 * byte_lane)
            exp_data = (current_data & ~byte_mask) | (tr.data & byte_mask)
            reg.predict(exp_data)

    def get_exp_response(self , t):
        """Return 1 for unmapped accesses and writes to the read-only ID."""
        # access unmapped register -> err
        try :
            register = self.reg_block._default_map.get_reg_by_offset(t.addr)
             # writing into read only register -> err
            rights = register.get_rights(self.reg_block._default_map)
            if rights == "RO" and t.dir == 1:
                return 1
        except AttributeError :
            return 1
        
        return 0

    def handle_reset(self):
        """Restore all mirrored register values to their reset state."""
        self.reg_block.reset("HARD")

class apb_scoreboard(uvm_scoreboard , reset_handler):
    """Compare monitor results against response and data predictions."""

    def __init__(self, name, parent=None):
        super().__init__(name, parent)
        self.port_in_response = apb_analysis_imp("port_in_response", self , self.write_in_response)
        self.port_in_data     = apb_analysis_imp("port_in_data" , self , self.write_in_data)
        self.port_in_agent    = apb_analysis_imp("port_in_agent" , self , self.write_in_agent)
        self.exp_response_fifo     = uvm_tlm_fifo("exp_resp_fifo" , self)
        self.exp_data_fifo         = uvm_tlm_fifo("exp_data_fifo" , self)
 
    def write_in_response(self , resp):
        """Queue the model's expected response status."""
        self.exp_response_fifo.try_put(resp)

    def write_in_data(self , data):
        """Queue expected read data; writes and errors have no data item."""
        self.exp_data_fifo.try_put(data)

    def write_in_agent(self ,item):
        """Consume predictions in transaction order and assert on mismatches."""
        (got_resp , exp_resp) = self.exp_response_fifo.try_get()
        (got_data , exp_data) = self.exp_data_fifo.try_get()
        assert exp_resp == item.response
        if got_data == True:
            assert exp_data == item.data

    def handle_reset(self):
        """Discard predictions that belong to an interrupted transaction."""
        self.exp_data_fifo.flush()
        self.exp_response_fifo.flush()

class apb_coverage(uvm_component , reset_handler):
    """Collect transaction and reset functional coverage."""

    def __init__(self, name, parent=None):
        super().__init__(name, parent)
        self.port_in = apb_analysis_imp("port_in" , self , self.write)
        self.dut = ConfigDB().get(self , "" , "dut")

    def handle_reset(self):
           self.sample_reset(self.dut.psel.value)

    def write(self , item):
            self.sample_item(item)
    
    # Per-transfer coverage: operation, response, address class, strobe,
    # data corner, and idle gap, plus crosses of these dimensions.
    cover_item = coverage_section(
        CoverPoint(name="apb_coverage.dir",
                   vname="dir",
                   xf= lambda item : item.dir,
                   bins=[0 , 1],
                   bins_labels=["APB_READ" , "APB_WRITE"]),

        CoverPoint(name="apb_coverage.slverr" , 
                   vname="response",
                   xf=lambda item : item.response,
                   bins=[0,1],
                   bins_labels=["APB_OK" , "APB_ERR"]),

        CoverPoint(name="apb_coverage.register",
                   vname="register",
                   xf=lambda item : item.addr,
                   bins=[i*4 for i in range(16)],
                   bins_labels=[f"REG_{i}" for i in range(16)]),

        CoverPoint(name="apb_coverage.reg_right",
                   vname="reg_right",
                   xf=lambda item: ("RO" if item.addr == 0x3C else
                                    "RW" if 0 <= item.addr < 0x3C and item.addr % 4 == 0
                                    else "UNMAPPED"),
                   bins=["RW" , "RO" , "UNMAPPED"],
                   bins_labels=["RW" , "RO" , "UNMAPPED"]),

        CoverPoint(name="apb_coverage.pstrb",
                   vname="pstrb",
                   xf=lambda item : item.pstrb,
                   bins=[i for i in range(0 , 0xF)]),

        CoverPoint(name="apb_coverage.data_corners",
                   vname="data",
                   xf= lambda item : "ALL_ZEROS" if item.data==0x0000_0000 else "ALL_ONES" if item.data == 0xFFFF_FFFF else "OTHER",
                   bins=["ALL_ZEROS" , "ALL_ONES" , "OTHER"],
                   bins_labels=["ALL_ZEROS" , "ALL_ONES" , "OTHER"]),

        CoverPoint(name="apb_coverage.gap",
                   vname="gap",
                   xf=lambda item : "back2back" if item.prev_item_delay == 0 else "has_gap",
                   bins=["back2back" , "has_gap"],
                   bins_labels=["BACK2BACK" , "HAS_GAP"]),

        CoverCross(name="apb_coverage.dir_x_reg_right",
                   items=["apb_coverage.dir" , "apb_coverage.reg_right"]),

        CoverCross(name="apb_coverage.valid_reg_x_dir",
                   items=["apb_coverage.register" , "apb_coverage.dir"],
                   ign_bins=[("REG_15","APB_WRITE")]),

        CoverCross(name="apb_coverage.pstrb_on_write",
                   items=["apb_coverage.pstrb" , "apb_coverage.dir" , "apb_coverage.register"],
                   ign_bins=[(None,"APB_READ",None)]),

        CoverCross(name="apb_coverage.pstrb_on_read",
                   items=["apb_coverage.pstrb" , "apb_coverage.dir"],
                   ign_bins=[(None , "APB_WRITE")]),

        CoverCross(name="apb_coverage.dir_x_slverr",
                   items=["apb_coverage.dir" , "apb_coverage.slverr"])         
    )

    @cover_item
    def sample_item(self, item):
        pass
    
    # Reset is sampled separately because it is not a completed APB transfer.
    cover_reset = coverage_section(
        CoverPoint(name="apb_coverage.reset_coverage",
                    vname="psel",
                    bins=[1 , 0],
                    xf= lambda psel : int(psel),
                    bins_labels=["RESET_ON_ACTIVE_TR" , "RESET_ON_IDLE_BUS"])
    )

    @cover_reset
    def sample_reset(self , psel):
        pass
   
    

        

class apb_env(uvm_env , reset_handler):
    """Instantiate and connect the active agent, model, scoreboard, coverage."""

    def __init__(self, name, parent=None):
        super().__init__(name, parent)

    def build_phase(self):
        super().build_phase()
        # Build the bus agent and the analysis-side checking components.
        self.agent     = apb_agent.create("agent" , self)
        self.model     = apb_model.create("model" , self)
        self.scb       = apb_scoreboard.create("scb" , self)
        #self.predictor = apb_predictor.create("predictor" , self)
        self.coverage  = apb_coverage.create("coverage" , self)
    def connect_phase(self):
        super().connect_phase()
        self.adapter = apb_adapter.create("adapter")
        # Route register-model operations through the APB sequencer and adapter.
        self.model.reg_block._default_map.set_sequencer(self.agent.sqr , self.adapter)
        self.model.reg_block._default_map.set_auto_predict(True)

        # The monitor sends request fields to the predictor before completion.
        self.agent.monitor.port_out_model.connect(self.model.input_port)

        # Completed transfers and model predictions meet at the scoreboard.
        self.agent.monitor.output_port.connect(self.scb.port_in_agent)
        self.model.output_port_response.connect(self.scb.port_in_response)
        self.model.output_port_data.connect(self.scb.port_in_data)

        # Sample the same completed transactions used for scoreboarding.
        self.agent.monitor.output_port.connect(self.coverage.port_in)

    async def wait_reset_start(self):
        await self.agent.wait_reset_start()

    async def wait_reset_end(self):
        await self.agent.wait_reset_end()

    async def run_phase(self):
        await super().run_phase()
        while True:
            await self.wait_reset_start()
            self.handle_reset()
            await self.wait_reset_end()

    def handle_reset(self):
        super().handle_reset()
        self.model.handle_reset()
        self.coverage.handle_reset()
        self.scb.handle_reset()

class apb_test_base(uvm_test):
    """Default randomized test: run 5,000 generated sequence items."""

    def __init__(self, name, parent=None):
        super().__init__(name, parent)
        self.num_sequences = 5000

    def build_phase(self):
        super().build_phase()
        self.env = apb_env.create("env" , self)

    async def run_phase(self):
        await super().run_phase()
        self.raise_objection()
        # Allow reset and UVM run-phase components to start before traffic.
        await Timer(100 , "ns")

        for _ in range(self.num_sequences):
            seq = apb_sequence_random.create("seq")
            await seq.start(self.env.agent.sqr)

        await Timer(500 , "ns")
        self.drop_objection()

class apb_test_reset_ongoing(apb_test_base):
    """Assert reset during a transfer, then verify traffic resumes afterward."""

    def __init__(self, name, parent=None):
        super().__init__(name, parent)
        self.num_sequences = 10

    async def run_phase(self):
        self.raise_objection()
        await Timer(100 , "ns")
        seq = apb_sequence_random.create("seq")
        cocotb.start_soon(seq.start(self.env.agent.sqr))
        await RisingEdge(self.env.agent.dut.psel)
        await Timer(1 ,"ns")
        self.env.agent.dut.presetn.value = 0
        await RisingEdge(self.env.agent.dut.pclk)
        self.env.agent.dut.presetn.value = 1
        await RisingEdge(self.env.agent.dut.pclk)

        for _ in range(10):
            seq = apb_sequence_random.create("seq")
            await seq.start(self.env.agent.sqr)
        
        self.drop_objection()

class apb_test_pstrb(apb_test_base):
    """Select the directed sequence that covers each byte strobe."""

    def __init__(self, name, parent=None):
        super().__init__(name, parent)
        self.num_sequences = 1
        uvm_factory().set_type_override_by_type(
            apb_sequence_random, apb_sequence_pstrb
        )


class apb_test_illegal(apb_test_base):
    """Select randomized accesses to unmapped addresses."""

    def __init__(self, name, parent=None):
        super().__init__(name, parent)
        self.num_sequences = 30
        uvm_factory().set_type_override_by_type(
            apb_sequence_random, apb_sequence_illegal
        )


class apb_test_ro(apb_test_base):
    """Select a write attempt to the read-only ID register."""

    def __init__(self, name, parent=None):
        super().__init__(name, parent)
        self.num_sequences = 1
        uvm_factory().set_type_override_by_type(
            apb_sequence_random, apb_sequence_ro
        )

class apb_test_all_ones(apb_test_base):
    """Select the all-ones register write/read sequence."""

    def __init__(self, name, parent=None):
        super().__init__(name, parent)
        self.num_sequences = 1
        uvm_factory().set_type_override_by_type(
            apb_sequence_random, apb_sequence_all_ones
        )

async def clock(dut):
    """Generate a 10 ns free-running clock."""
    while True:
        dut.pclk.value = 0
        await Timer(5 , "ns")
        dut.pclk.value = 1
        await Timer(5 , "ns")

async def run_apb_test(dut, test_name):
    """Initialize the DUT, run the requested pyuvm test, and export coverage."""
    dut.presetn.value = 0
    dut.psel.value    = 0
    dut.paddr.value   = 0
    dut.pstrb.value   = 0
    dut.pwrite.value  = 0
    dut.penable.value = 0
    cocotb.start_soon(clock(dut))
    await Timer(20, "ns")
    dut.presetn.value = 1
    # Components retrieve their DUT handles from ConfigDB during construction.
    ConfigDB().set(None , "uvm_test_top.env.agent","dut", dut)
    ConfigDB().set(None , "uvm_test_top.env.coverage","dut", dut)
    await uvm_root().run_test(test_name, keep_set={ConfigDB})
    coverage_file = os.environ.get("APB_COVERAGE_FILE")
    if coverage_file:
        # Create the output directory so callers can choose nested report paths.
        coverage_path = Path(coverage_file)
        coverage_path.parent.mkdir(parents=True, exist_ok=True)
        coverage_db.export_to_xml(str(coverage_path))


@cocotb.test()
async def test_random(dut):
    """Cocotb entry point for the base randomized regression."""
    await run_apb_test(dut, "apb_test_base")


@cocotb.test()
async def test_pstrb(dut):
    """Cocotb entry point for byte-strobe directed testing."""
    await run_apb_test(dut, "apb_test_pstrb")


@cocotb.test()
async def test_illegal(dut):
    """Cocotb entry point for unmapped-address testing."""
    await run_apb_test(dut, "apb_test_illegal")


@cocotb.test()
async def test_ro(dut):
    """Cocotb entry point for the read-only register test."""
    await run_apb_test(dut, "apb_test_ro")

@cocotb.test()
async def test_reset_on_going(dut):
    """Cocotb entry point for reset-during-transfer testing."""
    await run_apb_test(dut, "apb_test_reset_ongoing")

@cocotb.test()
async def test_all_ones(dut):
    """Cocotb entry point for all-ones register data testing."""
    await run_apb_test(dut, "apb_test_all_ones")