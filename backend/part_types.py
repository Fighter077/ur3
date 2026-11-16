"""Part classification shared by vision OCR and local label parsing."""
import re


def normalize_part_type(value):
    if not isinstance(value, str):
        return None
    return {"hdd": "HDD", "ram": "RAM", "optical": "optical"}.get(value.strip().lower())


def infer_part_type(fields):
    declared = normalize_part_type(fields.get("type"))
    if declared:
        return declared
    # Capacity or manufacturer alone cannot distinguish a disk from memory.
    text = " ".join(str(fields.get(key) or "") for key in ("raw_text", "speed", "model"))
    signals = set()
    if re.search(r"\b(?:DDR[2-5]L?|PC[2-5]L?[- ]?\d+[A-Z]*|(?:SO[- ]?)?DIMM|SDRAM)\b", text, re.I):
        signals.add("RAM")
    if re.search(r"\b(?:HDD|HARD\s+(?:DISK|DRIVE)|\d{4,5}\s*RPM)\b", text, re.I):
        signals.add("HDD")
    if re.search(r"\b(?:DVD|CD[- ]?(?:RW|ROM)|BD[- ]?(?:RE|ROM|R)|BLU[- ]?RAY|OPTICAL\s+(?:DIS[CK]|DRIVE))\b", text, re.I):
        signals.add("optical")
    return next(iter(signals)) if len(signals) == 1 else None


def detected_part_type(*results):
    types = {kind for result in results if (kind := infer_part_type(result))}
    return next(iter(types)) if len(types) == 1 else None
