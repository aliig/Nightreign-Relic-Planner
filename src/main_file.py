## ALL THIS SCRIPT IS FROM JTESTA AT GITHUB: https://github.com/jtesta/souls_givifier. A MODFIED VERSION OF THE SCRIPT TO HANDE DECRYPT AND ENCRYPT OF THE DS2 SL2 FILES.
#ALL THE CREDIT GOES TO JTESTA and Nordgaren: https://github.com/Nordgaren/ArmoredCore6SaveTransferTool


import os
import sys
import struct
import hashlib
import json
from tkinter import filedialog
from typing import Optional, Dict
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


#nightreign sl2 key
DS2_KEY = b'\x18\xF6\x32\x66\x05\xBD\x17\x8A\x55\x24\x52\x3A\xC0\xA0\xC6\x09'
DEBUG_MODE = True
input_file = None

def bytes_to_intstr(byte_array: bytes) -> str:
    ret = ''
    for _, i in enumerate(byte_array):
        ret += "%u," % i
    return ret[0:-1]


def debug(msg: str = '') -> None:
    if DEBUG_MODE:
        print(msg)


def calculate_md5(data: bytes) -> bytes:
    return hashlib.md5(data).digest()

IV_SIZE = 0x10
PADDING_SIZE = 0xC
START_OF_CHECKSUM_DATA = 4  # sizeof(int)
END_OF_CHECKSUM_DATA = PADDING_SIZE + 16  # 0xC + MD5 hash size (16 bytes) = 28

class BND4Entry:
    def __init__(self, raw_data: bytes, index: int, output_folder: str, size: int, offset: int, name_offset: int, footer_length: int, data_offset: int):
        self.index = index
        self._index = index
        self.size = size
        self.data_offset = data_offset
        self.footer_length = footer_length
        self._raw_data = raw_data
        self._encrypted_data = raw_data[offset:offset + size]
        self._decrypted_slot_path = output_folder
        self._name = f"USERDATA_{index:02d}"
        self._clean_data = b''

        # Extract IV from beginning of encrypted data
        self._iv = self._encrypted_data[:IV_SIZE]
        self._encrypted_payload = self._encrypted_data[IV_SIZE:]

    def decrypt(self) -> None:
        try:
            decryptor = Cipher(algorithms.AES(DS2_KEY), modes.CBC(self._iv)).decryptor()
            decrypted_raw = decryptor.update(self._encrypted_payload) + decryptor.finalize()

            self._clean_data = decrypted_raw


            if self._decrypted_slot_path:
                os.makedirs(self._decrypted_slot_path, exist_ok=True)
                output_path = os.path.join(self._decrypted_slot_path, self._name)
                with open(output_path, 'wb') as f:
                    f.write(self._clean_data)
            self.decrypted = True

        except Exception as e:
            print(f"Error decrypting entry {self._index}: {str(e)}")
            raise


def process_entries_in_order(entries):
        # Sort entries by index to ensure consistent processing order
        sorted_entries = sorted(entries, key=lambda e: e.index)

        for entry in sorted_entries:
            entry.decrypt()

        return sorted_entries

def save_index_mapping(entries, output_path):
    mapping = {}
    for entry in entries:
        if entry.decrypted:
            filename = f"USERDATA_{entry.index:02d}"
            mapping[entry.index] = filename

    mapping_file = os.path.join(output_path, "index_mapping.json")
    with open(mapping_file, 'w') as f:
        json.dump(mapping, f)

def get_input() -> Optional[str]:
    return filedialog.askopenfilename(
        title="Select Decrypted SL2 File",
        filetypes=[("SL2 Files", "*.sl2"), ("All Files", "*.*")]
    )


def decrypt_ds2_sl2(input_file, log_callback=None) -> Dict[int, str]:
    global original_sl2_path
    global input_decrypted_path
    global bnd4_entries
    global raw

    if not input_file:
        input_file = get_input()

    if not input_file:
        return None

    original_sl2_path = input_file

    def log(message):
        if log_callback:
            log_callback(message)
        debug(message)

    try:
        with open(input_file, 'rb') as f:
            raw = f.read()
    except Exception as e:
        log(f"ERROR: Could not read input file: {e}")
        return {}

    log(f"Read {len(raw)} bytes from {input_file}.")
    if raw[0:4] != b'BND4':
        log("ERROR: 'BND4' header not found! This doesn't appear to be a valid SL2 file.")
        return {}
    else:
        log("Found BND4 header.")

    num_bnd4_entries = struct.unpack("<i", raw[12:16])[0]
    log(f"Number of BND4 entries: {num_bnd4_entries}")

    unicode_flag = (raw[48] == 1)
    log(f"Unicode flag: {unicode_flag}")
    log("")

    BND4_HEADER_LEN = 64
    BND4_ENTRY_HEADER_LEN = 32

    slot_occupancy = {}
    bnd4_entries = []
    successful_decryptions = 0

    # Process all BND4 entries
    script_dir = os.path.dirname(os.path.abspath(__file__))
    output_folder = os.path.join(script_dir, "decrypted_output")
    input_decrypted_path = output_folder

    for i in range(num_bnd4_entries):
        pos = BND4_HEADER_LEN + (BND4_ENTRY_HEADER_LEN * i)

        if pos + BND4_ENTRY_HEADER_LEN > len(raw):
            log(f"Warning: File too small to read entry #{i} header")
            break

        entry_header = raw[pos:pos + BND4_ENTRY_HEADER_LEN]

        if entry_header[0:8] != b'\x40\x00\x00\x00\xff\xff\xff\xff':
            log(f"Warning: Entry header #{i} does not match expected magic value - skipping")
            continue

        entry_size = struct.unpack("<i", entry_header[8:12])[0]
        entry_data_offset = struct.unpack("<i", entry_header[16:20])[0]
        entry_name_offset = struct.unpack("<i", entry_header[20:24])[0]
        entry_footer_length = struct.unpack("<i", entry_header[24:28])[0]

        # Validity checks
        if entry_size <= 0 or entry_size > 1000000000:  # Sanity check for size
            log(f"Warning: Entry #{i} has invalid size: {entry_size} - skipping")
            continue

        if entry_data_offset <= 0 or entry_data_offset + entry_size > len(raw):
            log(f"Warning: Entry #{i} has invalid data offset: {entry_data_offset} - skipping")
            continue

        if entry_name_offset <= 0 or entry_name_offset >= len(raw):
            log(f"Warning: Entry #{i} has invalid name offset: {entry_name_offset} - skipping")
            continue



        try:
            entry = BND4Entry(
                raw_data=raw,
                index=i,
                output_folder=output_folder,
                size=entry_size,
                offset=entry_data_offset,
                name_offset=entry_name_offset,
                footer_length=entry_footer_length,
                data_offset=entry_data_offset
            )

            try:
                entry.decrypt()
                bnd4_entries.append(entry)
                successful_decryptions += 1
            except Exception as e:
                log(f"Error decrypting entry #{i}: {str(e)}")
                continue

        except Exception as e:
            log(f"Error processing entry #{i}: {str(e)}")
            continue


    save_index_mapping(bnd4_entries, input_decrypted_path)

    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "decrypted_output")
