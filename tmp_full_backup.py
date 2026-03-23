#This script was created by Gavin Campbell on 3/18/2026 and is intended to backup and refractor NI IO Trace logs when a DUT Failure occurs. 

"""
Scope
1. Capture timestamp of when this script is executed. This indicates when the DUT failure occurred.
2. Read the active NI I/O Trace file (dummy.txt) and extract the last 1000 lines to capture recent activity.
3. Locate and extract the most recent TRACE_ANCHOR entry to obtain a reliable internal timestamp.
4. Parse the anchor into structured fields (timestamp, host, event) for clarity.
5. Refract relevant NI I/O Trace entries (viWrite, viRead, Formatted viWrite) into a human-readable timeline.
6. Normalize device identifiers and reconstruct timestamps where possible.
7. Include source file path and metadata (if available) for traceability.
8. Output a single human-readable refracted text file containing header context and parsed trace data.

Notes:
- This script is failure-triggered and focuses only on recent trace context.
- Full trace files and additional logs are intentionally excluded.
"""
from __future__ import annotations
from pathlib import Path
from collections import deque
from datetime import datetime
import re
from Functions.find_container import find_container, find_token


CAPTURE_LINES = 1000
TRACE_FILE = Path(r"C:\Users\TR010153\OneDrive - SubCom, LLC\Documents\Python Projects\NI_Trace_Helper\dummy.000002.txt") #Replace with actual path. 
OUTPUT_FILE = Path(r"C:\Users\TR010153\OneDrive - SubCom, LLC\Documents\Python Projects\NI_Trace_Helper\refracted_trace.txt") #Replace with path that you want files. 
ANCHOR_REGGIE = re.compile(r"TRACE_ANCHOR\|(?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\|HOST=(?P<host>[^|]+)\|EVENT=(?P<event>[^\r\n|]+)")

#Read tail end of the trace file. 
def read_trace_tail(trace_file:Path, max_lines = CAPTURE_LINES) -> list[str]:
    #Reads the last `max_lines` from the trace file
    if not trace_file.is_file():
        raise FileNotFoundError(f"Trace file not found: {trace_file}")
    with trace_file.open("r", encoding="utf-8", errors="ignore") as f:
        return list(deque(f, max_lines))
#Extract the most recent anchor entry
def extract_recent_anchor(lines:list[str]) -> dict | None:
    #Searches for the most recent TRACE_ANCHOR entry in the provided lines
    for line in reversed(lines): #Look backwards
        match = ANCHOR_REGGIE.search(line)
        if match:
            return {
                "raw": line.strip(), #Raw line without extra whitespace
                "timestamp": match.group("timestamp"), #Strip timestamp
                "host": match.group("host"), #Strip host
                "event": match.group("event"), #Strip event
            }
    return None
def parse_trace_block(block_lines: list[str]) -> dict | None:
    #Parse one 4-line NI I/O Trace block: operation line, Process ID / Thread ID, Start Time / Call Duration and Status
    if len(block_lines) < 4:
        return None

    op_line = block_lines[0].strip()
    process_line = block_lines[1].strip()
    start_line = block_lines[2].strip()
    status_line = block_lines[3].strip()

    if not op_line:
        return None

    operation = None
    for target in ("Formatted viWrite", "viWrite", "viRead"):
        if target in op_line:
            operation = target
            break

    if operation is None:
        return None

    # Extract Start Time from the third line of the block
    time_match = re.search(r"Start Time:\s*(\d{2}:\d{2}:\d{2}(?:\.\d+)?)", start_line)
    time_str = time_match.group(1) if time_match else None

    # Extract VISA resource from operation line
    resource_match = re.search(
        r"(GPIB\d*::\d+(?:::[^ )]+)*)|(TCPIP\d*::[^ )]+(?:::[^ )]+)*)|(ASRL\d*::[^ )]+)|(USB\d*::[^ )]+)",
        op_line
    )
    resource = resource_match.group(0) if resource_match else "UNKNOWN"

    # Extract quoted payload from operation line
    quoted_parts = re.findall(r'"([^"]*)"', op_line)
    command = quoted_parts[-1] if quoted_parts else None
    command_quoted = f'"{command}"' if command is not None else '""'

    # Extract status code/text from line 4
    status_match = re.search(r"Status:\s*(.+)", status_line)
    status_text = status_match.group(1).strip() if status_match else None

    return {
        "raw_block": "".join(block_lines),
        "raw_operation_line": op_line,
        "time_str": time_str,
        "operation": operation,
        "resource": resource,
        "command": command,
        "command_quoted": command_quoted,
        "status": status_text,
        "process_line": process_line,
        "start_line": start_line,
        "status_line": status_line,
    }
def normalize_resource(resource): 
    #Convert raw VISA resource strings into a human friendly format. 
    if resource == "UNKNOWN":
        return resource
    normalized = resource.replace("::INSTR", "")
    normalized = normalized.replace("::SOCKET", "")
    normalized = normalized.replace("::", "_")
    return normalized
def parse_trace_entries(lines: list[str]) -> list[dict]:
    #Parse NI I/O Trace using fixed 4-line blocks. Only relevant operations are returned.
    parsed_entries = []

    i = 0
    while i + 3 < len(lines):
        block = lines[i:i+4]
        entry = parse_trace_block(block)

        if entry is not None:
            entry["device"] = normalize_resource(entry["resource"])
            parsed_entries.append(entry)
            i += 4
        else:
            # Advance by one line until a valid block start is found
            i += 1

    return parsed_entries

def collect_header_metadata() -> dict:
    """
    Collect failure-context metadata from StationGlobals containers.
    Missing values are allowed and returned as None.
    """
    metadata = {
        "DeviceID": None,
        "TestName": None,
        "Tester": None,
        "Tset_id": None,
        "ErrMsg": None,
        "CurrentTestTemp": None,
    }

    try:
        Tcond = find_container("Tcond")
        Master_Cfg = find_container("Master_Cfg")
        TestParams = find_container("TestParams")

        device_id, device_id_found = find_token("DeviceID", Tcond)
        test_name, test_name_found = find_token("TestName", TestParams)
        tester, tester_found = find_token("Tester", TestParams)
        tset_id, tset_id_found = find_token("Tset_id", Master_Cfg)
        err_msg, err_msg_found = find_token("ErrMsg", TestParams)
        current_test_temp, current_test_temp_found = find_token("CurrentTestTemp", TestParams)

        if device_id_found:
            metadata["DeviceID"] = device_id
        if test_name_found:
            metadata["TestName"] = test_name
        if tester_found:
            metadata["Tester"] = tester
        if tset_id_found:
            metadata["Tset_id"] = tset_id
        if err_msg_found:
            metadata["ErrMsg"] = err_msg
        if current_test_temp_found:
            metadata["CurrentTestTemp"] = current_test_temp

    except Exception as exc:
        metadata["MetadataWarning"] = f"Could not read StationGlobals metadata: {exc}"

    return metadata
def build_output_file_path(output_file: Path, latest_anchor: dict | None, capture_time: str) -> Path:
    #Build a unique output filename using the latest anchor timestamp if available, otherwise fall back to the script capture time.
    if latest_anchor:
        stamp = latest_anchor["timestamp"]
    else:
        stamp = capture_time

    safe_stamp = stamp.replace(":", ".").replace(" ", "_")
    stem = output_file.stem
    suffix = output_file.suffix or ".txt"

    return output_file.parent / f"{stem}_{safe_stamp}{suffix}"
def write_refracted_output(output_file: Path, capture_time:str, source_file: Path, lines_examined: int, latest_anchor: dict | None, parsed_entries: list[dict], metadata: dict):
    #Write refractored trace report. 
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with output_file.open("w", encoding="utf-8") as f:
        #Start header block with file name, lines examined, and total parsed entries.
        f.write(f"NI I/O Trace Refracted Report\n")
        f.write(f"Capture Time: {capture_time}\n")
        f.write(f"Source Trace File: {source_file}\n")
        f.write(f"Lines Examined: {lines_examined}\n")
        f.write(f"Total Parsed NI I/O Trace Entries: {len(parsed_entries)}\n")
        f.write(f"-----------------------------------------------------------------\n\n")

        #2nd part of header is test context metadata if it could be found.
        for key in ("DeviceID", "TestName", "Tester", "Tset_id", "CurrentTestTemp", "ErrMsg"):
            value = metadata.get(key)
            if value is not None and str(value).strip() != "":
                f.write(f"{key}: {value}\n")

        if metadata.get("MetadataWarning"):
            f.write(f"Metadata Warning: {metadata['MetadataWarning']}\n")
        f.write(f"-----------------------------------------------------------------\n\n")
        #3rd part of the header that lets user know if an anchor was found. 
        if latest_anchor:
            f.write("Anchor Found: Yes\n")
            f.write(f"Latest Anchor: {latest_anchor['raw']}\n")
            f.write(f"Anchor Time: {latest_anchor['timestamp']}\n")
            f.write(f"Anchor Host: {latest_anchor['host']}\n")
            f.write(f"Anchor Event: {latest_anchor['event']}\n")
            f.write(f"-----------------------------------------------------------------\n\n")
        else:
            f.write("Anchor Found: No\n")
            f.write("WARNING: No anchor found in the captured trace window.\n")
            f.write(f"-----------------------------------------------------------------\n\n")
        # Parsed trace body
        for entry in parsed_entries:
            display_time = entry.get("time_str") or "UNKNOWN_TIME"
            operation = entry.get("operation") or "UNKNOWN_OP"
            device = entry.get("device") or "UNKNOWN_DEVICE"
            command_quoted = entry.get("command_quoted") or '""'
            status = entry.get("status") or "UNKNOWN_STATUS"
            f.write(f"{display_time}\n")
            f.write(f"{operation} {device} {command_quoted}\n")
            f.write(f"Status: {status}\n")
            f.write("\n")
def main():
    capture_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    metadata = collect_header_metadata()

    trace_tail_lines = read_trace_tail(TRACE_FILE, CAPTURE_LINES)
    latest_anchor = extract_recent_anchor(trace_tail_lines)
    parsed_entries = parse_trace_entries(trace_tail_lines)
    actual_output_file = build_output_file_path(OUTPUT_FILE, latest_anchor, capture_time)

    print(f"Capture Time: {capture_time}")
    print(f"Source File: {TRACE_FILE}")
    print(f"Lines Read: {len(trace_tail_lines)}")
    print(f"Parsed Entries: {len(parsed_entries)}")

    if latest_anchor:
        print("Latest Anchor Found:")
        print(f"  Raw: {latest_anchor['raw']}")
        print(f"  Timestamp: {latest_anchor['timestamp']}")
        print(f"  Host: {latest_anchor['host']}")
        print(f"  Event: {latest_anchor['event']}")
    else:
        print("Latest Anchor Found: No")
        print("WARNING: No anchor found in last 1000 lines.")

    write_refracted_output(
        output_file=actual_output_file,
        capture_time=capture_time,
        source_file=TRACE_FILE,
        lines_examined=CAPTURE_LINES,
        latest_anchor=latest_anchor,
        parsed_entries=parsed_entries,
        metadata=metadata,
    )

    print(f"Refracted output written to: {actual_output_file}")
if __name__ == "__main__":
    main()
