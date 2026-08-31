import os
import sys
import argparse
import re
from difflib import SequenceMatcher

# --- Configuration ---
DOWNLOADS_DIR = "/mnt/external/downloads" # CHANGE THIS TO YOUR PERSONAL DOWNLOADS/LIBRARY FOLDERS
LIBRARY_DIR = "/mnt/external/library"

VIDEO_EXTENSIONS = {'.mkv', '.mp4', '.avi', '.mov', '.wmv', '.flv', '.webm', '.m4v'}

RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"

def should_skip_file(filepath):
    """Check if file should be skipped (sample, trailer, too small, etc.)"""
    filename = os.path.basename(filepath).lower()

    # Check folder names as path components, not substrings
    folder_path = os.path.dirname(filepath).lower()
    folder_parts = folder_path.split(os.sep)  # Split into individual folder names

    extra_folder_names = [
        'extras', 'extra',
        'featurettes', 'featurette',
        'samples', 'sample',
        'special features', 'special feature',
        'bonus', 'bonuses',
        'deleted scenes', 'deleted scene',
        'behind the scenes', 'behind-the-scenes', 'bts',
        'interviews', 'interview',
        'trailers', 'trailer',
        'previews', 'preview',
        'making of', 'making-of'
    ]

    for part in folder_parts:
        if part in extra_folder_names:
            return True, f"File is in '{part}' folder"

    # Check for very specific patterns that indicate samples/extras
    skip_patterns = [
        r'\.sample\.',           # .sample. in middle
        r'-sample\.',            # -sample. at end
        r'\.trailer\.',          # .trailer. in middle
        r'\[sample\]',           # [sample] in brackets
        r'\[trailer\]',          # [trailer] in brackets
        r'sample\.mp4',          # sample.mp4 at end
        r'trailer\.mp4',         # trailer.mp4 at end
        r'sample\.mkv',          # sample.mkv at end
        r'trailer\.mkv',         # trailer.mkv at end
    ]

    for pattern in skip_patterns:
        if re.search(pattern, filename):
            return True, f"Matches skip pattern '{pattern}'"

    return False, None

def parse_args():
    parser = argparse.ArgumentParser(description="Sync and hardlink media files between downloads and library.")
    parser.add_argument("--debug", action="store_true", help="Dry run: print actions without modifying files.")
    parser.add_argument("--verbose", action="store_true", help="Print all matching attempts.")
    return parser.parse_args()

def extract_media_info(filename):
    """Extracts the core title, year, and episode number from a media filename."""
    name, _ = os.path.splitext(filename)
    # Normalize separators to spaces
    name = name.replace('.', ' ').replace('_', ' ').replace('-', ' ')

    # Try to find Year (19xx or 20xx)
    year_match = re.search(r'\b(19|20)\d{2}\b', name)
    year = year_match.group(0) if year_match else None

    # Try to find Episode (SxxExx)
    ep_match = re.search(r'[Ss](\d{1,2})[Ee](\d{1,2})', name)
    ep = f"S{int(ep_match.group(1)):02d}E{int(ep_match.group(2)):02d}" if ep_match else None

    # Extract Title: everything before the Year or Episode
    if year_match:
        title_part = name[:year_match.start()]
    elif ep_match:
        title_part = name[:ep_match.start()]
    else:
        title_part = name

    # Clean title: remove brackets, non-alphanumeric chars, and extra spaces
    title = re.sub(r'\[.*?\]', '', title_part)
    title = re.sub(r'\(.*?\)', '', title_part)
    title = re.sub(r'[^a-zA-Z0-9\s]', '', title)
    title = ' '.join(title.split()).lower()

    return title, year, ep

def get_video_files(directory):
    """Recursively finds all video files in a directory."""
    files = []
    if not os.path.exists(directory):
        return files
    for root, _, filenames in os.walk(directory):
        for f in filenames:
            if os.path.splitext(f)[1].lower() in VIDEO_EXTENSIONS:
                files.append(os.path.join(root, f))
    return files

def is_hardlinked(path1, path2):
    """Checks if two files are hardlinked by comparing their inode numbers."""
    try:
        stat1 = os.stat(path1)
        stat2 = os.stat(path2)
        return stat1.st_ino == stat2.st_ino and stat1.st_dev == stat2.st_dev
    except OSError:
        return False

def find_best_match(download_file, library_files, verbose=False):
    """Finds the best matching library file for a given download file."""
    dl_name = os.path.basename(download_file)
    dl_title, dl_year, dl_ep = extract_media_info(dl_name)

    best_match = None
    highest_ratio = 0.0

    for lib_file in library_files:
        lib_name = os.path.basename(lib_file)
        lib_title, lib_year, lib_ep = extract_media_info(lib_name)

        # Guard 1: Year must match if both have it (Prevents matching Dune 2021 with Dune Part Two 2024)
        if dl_year and lib_year and dl_year != lib_year:
            continue

        # Guard 2: Episode must match if both have it (Prevents matching S04E10 with S04E09)
        if dl_ep and lib_ep and dl_ep != lib_ep:
            continue

        # Compare the extracted titles
        ratio = SequenceMatcher(None, dl_title, lib_title).ratio()

        # Boost ratio if one title is a substring of the other (handles "Dune" vs "Dune Part One")
        if dl_title and lib_title and (dl_title in lib_title or lib_title in dl_title):
            ratio = max(ratio, 0.95)

        if verbose:
            print(f"  [CHECK] DL: '{dl_title}' ({dl_year}, {dl_ep}) vs LIB: '{lib_title}' ({lib_year}, {lib_ep}) -> Ratio: {ratio:.2f}")

        if ratio > highest_ratio:
            highest_ratio = ratio
            best_match = lib_file

    # Require at least an 80% match
    if highest_ratio >= 0.80:
        return best_match, highest_ratio
    return None, highest_ratio

def main():
    args = parse_args()
    mode = "DEBUG (DRY RUN)" if args.debug else "LIVE"
    print(f"--- Starting Hardlink Sync in {mode} ---")
    print(f"Downloads: {DOWNLOADS_DIR}")
    print(f"Library:   {LIBRARY_DIR}\n")

    if not os.path.exists(DOWNLOADS_DIR) or not os.path.exists(LIBRARY_DIR):
        print("Error: One or both directories do not exist. Check paths.")
        sys.exit(1)

    print("Indexing library files...")
    lib_files = get_video_files(LIBRARY_DIR)
    print(f"Found {len(lib_files)} video files in library.\n")

    print("Scanning downloads and matching...")
    dl_files = get_video_files(DOWNLOADS_DIR)
    processed_count = 0
    skipped_count = 0
    hardlinked_count = 0

    for dl_file in dl_files:
        # Check for skip keywords
        skip, reason = should_skip_file(dl_file)
        if skip:
            print(f"Skipping {dl_file}: '{reason}'")
            continue

        match, ratio = find_best_match(dl_file, lib_files, args.verbose)

        if not match:
            skipped_count += 1
            continue

        if is_hardlinked(dl_file, match):
            skipped_count += 1
            continue

        processed_count += 1
        print(f"\n{GREEN}{BOLD}[MATCH FOUND]{RESET} (Similarity: {ratio:.0%})")
        print(f"{CYAN}  Download:{RESET} {os.path.basename(dl_file)}")
        print(f"{CYAN}  Library: {RESET} {os.path.basename(match)}")

        if args.debug:
                print(f"{YELLOW}  -> [DEBUG]{RESET} Would delete library file: {match}")
                print(f"{YELLOW}  -> [DEBUG]{RESET} Would create hardlink: {dl_file} -> {match}")
        else:
            try:
                os.remove(match)
                print(f"{GREEN}  ✓ Deleted:{RESET} {match}")

                os.link(dl_file, match)
                print(f"{GREEN}  ✓ Hardlinked:{RESET} {dl_file} -> {match}")
                hardlinked_count += 1
            except Exception as e:
                print(f"{RED}  ✗ ERROR:{RESET} Failed to process {match}: {e}")

            print(f"\n{BOLD}--- Summary ---{RESET}")
            print(f"Total downloads scanned: {len(dl_files)}")
        print(f"Files already hardlinked or unmatched: {skipped_count}")

        if args.debug:
                print(f"{YELLOW}Files that WOULD be hardlinked:{RESET} {processed_count}")
        else:
                print(f"{GREEN}Files successfully hardlinked:{RESET} {hardlinked_count}")
if __name__ == "__main__":
    main()