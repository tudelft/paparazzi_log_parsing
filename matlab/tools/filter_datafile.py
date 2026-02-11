#!/usr/bin/env python3
"""
Filter a Paparazzi .data file to include only specified message types.
This is useful for reducing file size before loading into MATLAB.

Usage:
    python filter_datafile.py input.data output.data MSG1 MSG2 MSG3 ...
    
Example:
    python filter_datafile.py large_log.data filtered.data IMU_GYRO_SCALED IMU_ACCEL_SCALED GPS ROTORCRAFT_FP
"""

import sys
import os


def filter_datafile(input_path, output_path, whitelist):
    """
    Filter a .data file to keep only messages in the whitelist.
    Also drops all data before the first motors_on event.
    
    Args:
        input_path: Path to input .data file
        output_path: Path to output .data file
        whitelist: Set of message names to keep
    """
    # Convert whitelist to set for O(1) lookup
    whitelist_set = set(whitelist) if whitelist else None

    # Always keep these messages (even before motors_on)
    always_keep_msgs = {
        'ROTORCRAFT_STATUS',
        'SETTING',
        'DL_SETTING',
        'GET_SETTING',
        'GET_DL_SETTING',
        'INFO_MSG',
    }

    # Always include required messages when using a whitelist
    if whitelist_set is not None:
        whitelist_set.update(always_keep_msgs)
    
    lines_read = 0
    lines_kept = 0
    motors_on_timestamp = None
    
    print(f"Filtering {input_path}...")
    if whitelist_set is None:
        print("Mode: Keep all messages, prune pre-motors_on data only")
    else:
        print(f"Whitelist: {', '.join(sorted(whitelist_set))}")
    print("Note: Always keeping setting messages and ROTORCRAFT_STATUS before motors_on")
    
    try:
        # First pass: find motors_on timestamp
        print("Pass 1: Finding motors_on event...")
        first_status = None
        with open(input_path, 'r') as infile:
            for line in infile:
                parts = line.strip().split(None, 3)
                
                if len(parts) >= 4 and parts[2] == 'ROTORCRAFT_STATUS':
                    # Parse ROTORCRAFT_STATUS fields
                    # Format: timestamp aircraftID ROTORCRAFT_STATUS ap_motors_on ap_in_flight mode ...
                    fields = parts[3].split()
                    
                    if first_status is None:
                        # Store first status to compare against
                        first_status = fields[:3] if len(fields) >= 3 else fields
                        continue
                    
                    # Check if any of the first 3 fields changed (motors_on, in_flight, or mode)
                    if len(fields) >= 3:
                        current_status = fields[:3]
                        if current_status != first_status:
                            motors_on_timestamp = float(parts[0])
                            print(f"  Status changed at timestamp: {motors_on_timestamp:.3f}")
                            print(f"    From: {' '.join(first_status)}")
                            print(f"    To:   {' '.join(current_status)}")
                            break
        
        if motors_on_timestamp is None:
            print("  Warning: No status change detected, keeping all data")
            motors_on_timestamp = 0.0
        
        # Second pass: filter and write data
        print("Pass 2: Filtering data...")
        with open(input_path, 'r') as infile, open(output_path, 'w') as outfile:
            for line in infile:
                lines_read += 1
                
                # Parse line format: timestamp aircraftID msgName msgContent
                parts = line.strip().split(None, 3)
                
                if len(parts) >= 3:
                    timestamp = float(parts[0])
                    msg_name = parts[2]
                    
                    # Keep line if message is always-keep, or after motors_on and in whitelist
                    if msg_name in always_keep_msgs:
                        outfile.write(line)
                        lines_kept += 1
                    elif timestamp >= motors_on_timestamp:
                        if whitelist_set is None or msg_name in whitelist_set:
                            outfile.write(line)
                            lines_kept += 1
                
                # Progress update every 500k lines
                if lines_read % 500000 == 0:
                    percent = 100 * lines_kept / lines_read if lines_read > 0 else 0
                    print(f"  Processed {lines_read:,} lines, kept {lines_kept:,} ({percent:.1f}%)")
        
        # Final statistics
        percent = 100 * lines_kept / lines_read if lines_read > 0 else 0
        reduction = 100 * (1 - lines_kept / lines_read) if lines_read > 0 else 0
        
        print(f"\nComplete:")
        print(f"  Total lines read: {lines_read:,}")
        print(f"  Lines kept: {lines_kept:,} ({percent:.1f}%)")
        print(f"  Reduction: {reduction:.1f}%")
        
        # File size comparison
        input_size_mb = os.path.getsize(input_path) / (1024 * 1024)
        output_size_mb = os.path.getsize(output_path) / (1024 * 1024)
        size_reduction = 100 * (1 - output_size_mb / input_size_mb) if input_size_mb > 0 else 0
        
        print(f"  Input file size: {input_size_mb:.1f} MB")
        print(f"  Output file size: {output_size_mb:.1f} MB")
        print(f"  Size reduction: {size_reduction:.1f}%")
        
    except FileNotFoundError:
        print(f"Error: Input file '{input_path}' not found")
        sys.exit(1)
    except PermissionError as e:
        print(f"Error: Permission denied - {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


def main():
    if len(sys.argv) < 3:
        print("Usage: python filter_datafile.py input.data output.data [MSG1 MSG2 MSG3 ...]")
        print("\nExamples:")
        print("  # Filter by message types:")
        print("  python filter_datafile.py large_log.data filtered.data IMU_GYRO_SCALED GPS")
        print("\n  # Keep all messages, only prune pre-motors_on data:")
        print("  python filter_datafile.py large_log.data filtered.data")
        sys.exit(1)
    
    input_path = sys.argv[1]
    output_path = sys.argv[2]
    whitelist = sys.argv[3:] if len(sys.argv) > 3 else []
    
    # Validate input file exists
    if not os.path.exists(input_path):
        print(f"Error: Input file '{input_path}' does not exist")
        sys.exit(1)
    
    # Warn if output file exists
    if os.path.exists(output_path):
        response = input(f"Warning: '{output_path}' already exists. Overwrite? (y/n): ")
        if response.lower() != 'y':
            print("Cancelled.")
            sys.exit(0)
    
    filter_datafile(input_path, output_path, whitelist)


if __name__ == "__main__":
    main()
