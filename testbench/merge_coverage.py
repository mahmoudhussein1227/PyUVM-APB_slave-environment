import argparse
from pathlib import Path
from xml.etree import ElementTree

from cocotb_coverage.coverage import merge_coverage


def main():
    parser = argparse.ArgumentParser(
        description="Merge Cocotb functional coverage XML files."
    )
    parser.add_argument("output", type=Path)
    parser.add_argument("inputs", nargs="+", type=Path)
    args = parser.parse_args()

    missing_files = [str(path) for path in args.inputs if not path.is_file()]
    if missing_files:
        parser.error(f"Coverage input file(s) not found: {', '.join(missing_files)}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    merge_coverage(print, str(args.output), *(str(path) for path in args.inputs))

    root = ElementTree.parse(args.output).getroot()
    covered = int(root.attrib["coverage"])
    size = int(root.attrib["size"])
    percentage = float(root.attrib["cover_percentage"])
    print(f"Overall functional coverage: {covered}/{size} ({percentage:.2f}%)")

    uncovered_bins = [
        (coverage_bin.attrib["abs_name"], coverage_bin.attrib["bin"])
        for coverage_item in root.iter()
        for coverage_bin in coverage_item
        if coverage_bin.tag.startswith("bin")
        and int(coverage_bin.attrib["hits"]) == 0
    ]
    if uncovered_bins:
        print("Uncovered bins:")
        for name, bin_value in uncovered_bins:
            print(f"  {name}: {bin_value}")


if __name__ == "__main__":
    main()
