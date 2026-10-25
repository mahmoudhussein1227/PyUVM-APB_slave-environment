// -----------------------------------------------------------------------------
// APB slave: 16 x 32-bit register block
//
//   Address map (word aligned, PADDR[1:0] ignored)
//     0x00 - 0x38 : REG0..REG14  read/write, reset value 0
//     0x3C        : ID register  read-only  (reads ID_VALUE)
//     0x40 - 0xFF : unmapped
//
//   Behaviour
//     * Byte writes honour PSTRB.
//     * PSLVERR is asserted (together with PREADY) when:
//         - a write targets the read-only ID register  (write is ignored)
//         - any access targets an unmapped address     (write ignored, read = 0)
//     * WAIT_CYCLES inserts that many wait states (PREADY low) in every
//       access phase. 0 = zero-wait-state slave.
// -----------------------------------------------------------------------------
module apb_slave #(
    parameter        WAIT_CYCLES = 1,
    parameter [31:0] ID_VALUE    = 32'hA9B0_0001
) (
    input  wire        pclk,
    input  wire        presetn,
    input  wire        psel,
    input  wire        penable,
    input  wire        pwrite,
    input  wire [7:0]  paddr,
    input  wire [31:0] pwdata,
    input  wire [3:0]  pstrb,
    output wire [31:0] prdata,
    output wire        pready,
    output wire        pslverr
);

    reg [31:0] regs [0:15];   // regs[15] is never written; ID is muxed on read
    reg [7:0]  wcnt;
    reg        wait_done;
    reg [31:0] tmp;
    integer    i, b;

    wire [3:0] idx     = paddr[5:2];
    wire       addr_ok = (paddr[7:6] == 2'b00);
    wire       is_ro   = (idx == 4'd15);

    wire setup  = psel & ~penable;
    wire access = psel &  penable;

    assign pready  = access & ((WAIT_CYCLES == 0) | wait_done);
    wire   err     = ~addr_ok | (pwrite & is_ro);
    assign pslverr = pready & err;

    assign prdata  = (pready & ~pwrite & addr_ok) ? (is_ro ? ID_VALUE : regs[idx])
                                                  : 32'd0;

    wire do_write = access & pready & pwrite & ~err;

    // wait-state counter
    always @(posedge pclk or negedge presetn) begin
        if (!presetn)                wcnt <= 8'd0;
        else if (setup)              wcnt <= 8'd0;
        else if (access && !pready)  wcnt <= wcnt + 8'd1;
    end

    always @(posedge pclk or negedge presetn) begin
        if (!presetn) begin
            wait_done <= 1'b0;
        end else if (!access) begin
            wait_done <= 1'b0;
        end else if (!wait_done && (WAIT_CYCLES > 0) &&
                     (WAIT_CYCLES <= 255) && (wcnt >= WAIT_CYCLES - 1)) begin
            // Register the threshold crossing so PREADY cannot glitch on a counter transition.
            wait_done <= 1'b1;
        end
    end

    // register file
    always @(posedge pclk or negedge presetn) begin
        if (!presetn) begin
            for (i = 0; i < 16; i = i + 1) regs[i] <= 32'd0;
        end else if (do_write) begin
            tmp = regs[idx];
            for (b = 0; b < 4; b = b + 1)
                if (pstrb[b]) tmp[8*b +: 8] = pwdata[8*b +: 8];
            regs[idx] <= tmp;
        end
    end

endmodule
