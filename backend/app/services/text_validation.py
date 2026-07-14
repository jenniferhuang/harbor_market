from __future__ import annotations


def validate_xml_safe_text(value: str, *, label: str, max_length: int) -> str:
    if len(value) > max_length:
        raise ValueError(f"{label} cannot exceed {max_length} characters")
    if any(ord(character) < 32 and character not in "\t\n\r" for character in value):
        raise ValueError(f"{label} contains an unsupported control character")
    return value
