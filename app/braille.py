"""Chuyển đổi văn bản sang Unicode Braille Grade 1 và định dạng BRF."""
from __future__ import annotations

# Bảng chữ cái Braille Grade 1 (dots 1-6)
# Mỗi ký tự ánh xạ sang mặt nạ bit (bit 0 = dot 1, bit 5 = dot 6)
_ALPHA: dict[str, int] = {
    'a': 0b000001, 'b': 0b000011, 'c': 0b001001, 'd': 0b011001, 'e': 0b010001,
    'f': 0b001011, 'g': 0b011011, 'h': 0b010011, 'i': 0b001010, 'j': 0b011010,
    'k': 0b000101, 'l': 0b000111, 'm': 0b001101, 'n': 0b011101, 'o': 0b010101,
    'p': 0b001111, 'q': 0b011111, 'r': 0b010111, 's': 0b001110, 't': 0b011110,
    'u': 0b100101, 'v': 0b100111, 'w': 0b111010, 'x': 0b101101, 'y': 0b111101,
    'z': 0b110101,
}

_DIGITS: dict[str, int] = {
    '1': 0b000001, '2': 0b000011, '3': 0b001001, '4': 0b011001, '5': 0b010001,
    '6': 0b001011, '7': 0b011011, '8': 0b010011, '9': 0b001010, '0': 0b011010,
}

_PUNCT: dict[str, int] = {
    '.': 0b110010, ',': 0b000010, '?': 0b100110, '!': 0b010110,
    ';': 0b000110, ':': 0b010010, "'": 0b000100, '"': 0b100010,
    '-': 0b100100, '(': 0b110011, ')': 0b111100,
}

NUMBER_INDICATOR = 0b111100  # dots 3,4,5,6 — bật chế độ số
CAPITAL_INDICATOR = 0b100000  # dot 6 — ký tự hoa tiếp theo

UNICODE_BRAILLE_BASE = 0x2800


def _cell_to_unicode(mask: int) -> str:
    """Chuyển mặt nạ 6-bit sang ký tự Unicode Braille."""
    # Chuẩn Unicode Braille dùng bit theo thứ tự 1,2,4,8,16,32,64,128 → dot 1-8
    # dots 1-6 → bit positions 0-5 → unicode offset giữ nguyên
    return chr(UNICODE_BRAILLE_BASE | mask)


def _cell_to_brf(mask: int) -> str:
    """Chuyển mặt nạ 6-bit sang ký tự BRF ASCII."""
    # BRF: space=32, ký tự Braille = 32 + mask
    return chr(32 + mask)


def text_to_unicode_braille(text: str) -> str:
    """Chuyển chuỗi văn bản (Latin/English) sang Unicode Braille."""
    result: list[str] = []
    in_number = False

    for ch in text:
        if ch == ' ':
            result.append(_cell_to_unicode(0))  # ô trắng
            in_number = False
        elif ch == '\n':
            result.append('\n')
            in_number = False
        elif ch.isdigit():
            if not in_number:
                result.append(_cell_to_unicode(NUMBER_INDICATOR))
                in_number = True
            result.append(_cell_to_unicode(_DIGITS[ch]))
        elif ch.isalpha():
            in_number = False
            lo = ch.lower()
            if ch.isupper():
                result.append(_cell_to_unicode(CAPITAL_INDICATOR))
            if lo in _ALPHA:
                result.append(_cell_to_unicode(_ALPHA[lo]))
            else:
                result.append(_cell_to_unicode(0))  # ký tự không hỗ trợ
        elif ch in _PUNCT:
            in_number = False
            result.append(_cell_to_unicode(_PUNCT[ch]))
        else:
            in_number = False
            result.append(ch)  # giữ nguyên ký tự không nhận ra

    return ''.join(result)


def text_to_brf(text: str) -> str:
    """Chuyển văn bản sang BRF (Braille Ready Format — ASCII Braille)."""
    result: list[str] = []
    in_number = False

    for ch in text:
        if ch == ' ':
            result.append(' ')
            in_number = False
        elif ch == '\n':
            result.append('\r\n')
            in_number = False
        elif ch.isdigit():
            if not in_number:
                result.append(_cell_to_brf(NUMBER_INDICATOR))
                in_number = True
            result.append(_cell_to_brf(_DIGITS[ch]))
        elif ch.isalpha():
            in_number = False
            lo = ch.lower()
            if ch.isupper():
                result.append(_cell_to_brf(CAPITAL_INDICATOR))
            if lo in _ALPHA:
                result.append(_cell_to_brf(_ALPHA[lo]))
        elif ch in _PUNCT:
            in_number = False
            result.append(_cell_to_brf(_PUNCT[ch]))
        else:
            in_number = False

    return ''.join(result)


def vietnamese_note() -> str:
    return (
        "⚠️ Lưu ý: Chữ Braille tiếng Việt đầy đủ (6 thanh + dấu) cần thư viện liblouis "
        "với bảng vi-g1.ctb. Phiên bản hiện tại chuyển đổi ký tự Latin cơ bản (không dấu). "
        "Để hỗ trợ tiếng Việt đầy đủ: apt install liblouis-dev && pip install louis"
    )
