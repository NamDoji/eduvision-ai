"""Chuyển đổi văn bản sang Unicode Braille — hỗ trợ tiếng Việt đầy đủ.

Tiêu chuẩn: TCVN 10734:2016 (Chữ Braille tiếng Việt) — Grade 1.
Mỗi ký tự tiếng Việt (gồm dấu và thanh) được ánh xạ trực tiếp vào ô Braille.
"""
from __future__ import annotations

import unicodedata

UNICODE_BRAILLE_BASE = 0x2800

# ── Bảng Braille Grade 1 tiếng Việt (TCVN 10734:2016) ─────────────────────
# Ký tự → mặt nạ 6-bit (dot 1-6 theo thứ tự bit 0-5)
# Cách đọc mặt nạ: bit0=dot1(trên-trái), bit1=dot2, bit2=dot3,
#                   bit3=dot4(trên-phải), bit4=dot5, bit5=dot6
BRAILLE_MAP: dict[str, int] = {
    # Chữ thường cơ bản (Latin, theo Grade 1)
    'a': 0b000001, 'b': 0b000011, 'c': 0b001001, 'd': 0b011001, 'e': 0b010001,
    'f': 0b001011, 'g': 0b011011, 'h': 0b010011, 'i': 0b001010, 'j': 0b011010,
    'k': 0b000101, 'l': 0b000111, 'm': 0b001101, 'n': 0b011101, 'o': 0b010101,
    'p': 0b001111, 'q': 0b011111, 'r': 0b010111, 's': 0b001110, 't': 0b011110,
    'u': 0b100101, 'v': 0b100111, 'w': 0b111010, 'x': 0b101101, 'y': 0b111101,
    'z': 0b110101,

    # Chữ cái tiếng Việt có dấu phụ (TCVN 10734:2016)
    'ă': 0b100011, 'â': 0b110001, 'ê': 0b110011, 'ô': 0b101011,
    'ơ': 0b111001, 'ư': 0b111011, 'đ': 0b011110,

    # Ký tự ghép âm đầu tiếng Việt (consonant clusters)
    'ch': 0b101111, 'gh': 0b111100, 'gi': 0b110111, 'kh': 0b110010,
    'ng': 0b110110, 'ngh': 0b111110, 'nh': 0b110100, 'ph': 0b101100,
    'qu': 0b111000, 'th': 0b101000, 'tr': 0b100110, 'xi': 0b011000,

    # Dấu thanh (tone marks) — tiền tố đứng trước âm tiết
    # Không dấu: không có tiền tố
    'TONE_SAC':    0b000010,   # ́  (sắc)
    'TONE_HUYEN':  0b100000,   # ̀  (huyền)
    'TONE_HOI':    0b000110,   # ̉  (hỏi)
    'TONE_NGA':    0b010000,   # ̃  (ngã)
    'TONE_NANG':   0b100010,   # ̣  (nặng)

    # Số
    'NUM_IND': 0b111100,  # chỉ thị số
    '0': 0b011010, '1': 0b000001, '2': 0b000011, '3': 0b001001,
    '4': 0b011001, '5': 0b010001, '6': 0b001011, '7': 0b011011,
    '8': 0b010011, '9': 0b001010,

    # Dấu câu
    '.': 0b110010, ',': 0b000010, '?': 0b100110, '!': 0b010110,
    ';': 0b000110, ':': 0b010010, "'": 0b000100, '"': 0b100010,
    '-': 0b100100, '(': 0b110011, ')': 0b111100,
    '/': 0b110000, '…': 0b110110,

    # Chỉ thị chữ hoa
    'CAP_IND': 0b100000,
}

# Bảng dấu thanh Unicode → key TONE_*
_TONE_MAP: dict[str, str] = {
    '́': 'TONE_SAC',    # combining acute
    '̀': 'TONE_HUYEN',  # combining grave
    '̉': 'TONE_HOI',    # combining hook above
    '̃': 'TONE_NGA',    # combining tilde
    '̣': 'TONE_NANG',   # combining dot below
}

# Dấu phụ (base modifier) Unicode — gộp vào ký tự gốc
_BASE_MOD: dict[str, str] = {
    '̆': 'breve',   # ̆ → ă
    '̂': 'circ',    # ̂ → â, ê, ô
    '̛': 'horn',    # ̛ → ơ, ư
}

# Precomposed Vietnamese → normalised base + mod key
_VI_PRECOMPOSED: dict[str, str] = {
    'ă': 'ă', 'Ă': 'ă',
    'â': 'â', 'Â': 'â',
    'ê': 'ê', 'Ê': 'ê',
    'ô': 'ô', 'Ô': 'ô',
    'ơ': 'ơ', 'Ơ': 'ơ',
    'ư': 'ư', 'Ư': 'ư',
    'đ': 'đ', 'Đ': 'đ',
    # vowels with tones — map to base vowel (tone handled separately)
    'à':'a','á':'a','ả':'a','ã':'a','ạ':'a',
    'ầ':'â','ấ':'â','ẩ':'â','ẫ':'â','ậ':'â',
    'ằ':'ă','ắ':'ă','ẳ':'ă','ẵ':'ă','ặ':'ă',
    'è':'e','é':'e','ẻ':'e','ẽ':'e','ẹ':'e',
    'ề':'ê','ế':'ê','ể':'ê','ễ':'ê','ệ':'ê',
    'ì':'i','í':'i','ỉ':'i','ĩ':'i','ị':'i',
    'ò':'o','ó':'o','ỏ':'o','õ':'o','ọ':'o',
    'ồ':'ô','ố':'ô','ổ':'ô','ỗ':'ô','ộ':'ô',
    'ờ':'ơ','ớ':'ơ','ở':'ơ','ỡ':'ơ','ợ':'ơ',
    'ù':'u','ú':'u','ủ':'u','ũ':'u','ụ':'u',
    'ừ':'ư','ứ':'ư','ử':'ư','ữ':'ư','ự':'ư',
    'ỳ':'y','ý':'y','ỷ':'y','ỹ':'y','ỵ':'y',
}

# Bảng tông thanh của ký tự precomposed
_VI_TONE: dict[str, str] = {
    # huyền
    'à':'TONE_HUYEN','ầ':'TONE_HUYEN','ằ':'TONE_HUYEN','è':'TONE_HUYEN',
    'ề':'TONE_HUYEN','ì':'TONE_HUYEN','ò':'TONE_HUYEN','ồ':'TONE_HUYEN',
    'ờ':'TONE_HUYEN','ù':'TONE_HUYEN','ừ':'TONE_HUYEN','ỳ':'TONE_HUYEN',
    # sắc
    'á':'TONE_SAC','ấ':'TONE_SAC','ắ':'TONE_SAC','é':'TONE_SAC',
    'ế':'TONE_SAC','í':'TONE_SAC','ó':'TONE_SAC','ố':'TONE_SAC',
    'ớ':'TONE_SAC','ú':'TONE_SAC','ứ':'TONE_SAC','ý':'TONE_SAC',
    # hỏi
    'ả':'TONE_HOI','ẩ':'TONE_HOI','ẳ':'TONE_HOI','ẻ':'TONE_HOI',
    'ể':'TONE_HOI','ỉ':'TONE_HOI','ỏ':'TONE_HOI','ổ':'TONE_HOI',
    'ở':'TONE_HOI','ủ':'TONE_HOI','ử':'TONE_HOI','ỷ':'TONE_HOI',
    # ngã
    'ã':'TONE_NGA','ẫ':'TONE_NGA','ẵ':'TONE_NGA','ẽ':'TONE_NGA',
    'ễ':'TONE_NGA','ĩ':'TONE_NGA','õ':'TONE_NGA','ỗ':'TONE_NGA',
    'ỡ':'TONE_NGA','ũ':'TONE_NGA','ữ':'TONE_NGA','ỹ':'TONE_NGA',
    # nặng
    'ạ':'TONE_NANG','ậ':'TONE_NANG','ặ':'TONE_NANG','ẹ':'TONE_NANG',
    'ệ':'TONE_NANG','ị':'TONE_NANG','ọ':'TONE_NANG','ộ':'TONE_NANG',
    'ợ':'TONE_NANG','ụ':'TONE_NANG','ự':'TONE_NANG','ỵ':'TONE_NANG',
}


def _mask_to_unicode(mask: int) -> str:
    return chr(UNICODE_BRAILLE_BASE | mask)


def _mask_to_brf(mask: int) -> str:
    return chr(32 + mask)


def _encode_char(ch: str, converter) -> list[int]:
    """Chuyển một ký tự (Latin hoặc tiếng Việt) thành danh sách mặt nạ Braille."""
    cells: list[int] = []

    # Chữ hoa → chỉ thị CAP
    if ch.isupper() and ch not in ('Ă','Â','Ê','Ô','Ơ','Ư','Đ'):
        cells.append(BRAILLE_MAP['CAP_IND'])

    lo = ch.lower()

    # Ký tự tiếng Việt precomposed (có thanh)
    if ch in _VI_TONE:
        tone_key = _VI_TONE[ch]
        cells.append(BRAILLE_MAP[tone_key])

    # Base ký tự (tiếng Việt biến thể hoặc thường)
    base = _VI_PRECOMPOSED.get(ch, lo)
    if base in BRAILLE_MAP:
        cells.append(BRAILLE_MAP[base])
    elif lo in BRAILLE_MAP:
        cells.append(BRAILLE_MAP[lo])

    return cells


def text_to_unicode_braille(text: str) -> str:
    """Chuyển văn bản tiếng Việt/Anh sang Unicode Braille."""
    result: list[str] = []
    in_number = False
    i = 0
    while i < len(text):
        ch = text[i]

        if ch in (' ', '\t'):
            result.append(_mask_to_unicode(0))
            in_number = False
            i += 1
            continue

        if ch == '\n':
            result.append('\n')
            in_number = False
            i += 1
            continue

        if ch.isdigit():
            if not in_number:
                result.append(_mask_to_unicode(BRAILLE_MAP['NUM_IND']))
                in_number = True
            result.append(_mask_to_unicode(BRAILLE_MAP[ch]))
            i += 1
            continue

        in_number = False

        if ch in BRAILLE_MAP or ch in _VI_PRECOMPOSED or ch.lower() in BRAILLE_MAP:
            for mask in _encode_char(ch, _mask_to_unicode):
                result.append(_mask_to_unicode(mask))
            i += 1
            continue

        if ch in BRAILLE_MAP:
            result.append(_mask_to_unicode(BRAILLE_MAP[ch]))
            i += 1
            continue

        result.append(ch)
        i += 1

    return ''.join(result)


def text_to_brf(text: str) -> str:
    """Chuyển văn bản sang BRF (ASCII Braille, dùng cho thiết bị đọc Braille)."""
    result: list[str] = []
    in_number = False
    i = 0
    while i < len(text):
        ch = text[i]

        if ch in (' ', '\t'):
            result.append(' ')
            in_number = False
            i += 1
            continue

        if ch == '\n':
            result.append('\r\n')
            in_number = False
            i += 1
            continue

        if ch.isdigit():
            if not in_number:
                result.append(_mask_to_brf(BRAILLE_MAP['NUM_IND']))
                in_number = True
            result.append(_mask_to_brf(BRAILLE_MAP[ch]))
            i += 1
            continue

        in_number = False

        if ch in BRAILLE_MAP or ch in _VI_PRECOMPOSED or ch.lower() in BRAILLE_MAP:
            for mask in _encode_char(ch, _mask_to_brf):
                result.append(_mask_to_brf(mask))
            i += 1
            continue

        if ch in BRAILLE_MAP:
            result.append(_mask_to_brf(BRAILLE_MAP[ch]))
            i += 1
            continue

        i += 1  # bỏ qua ký tự không xác định

    return ''.join(result)


def vietnamese_note() -> str:
    return (
        "Chữ Braille tiếng Việt theo TCVN 10734:2016 — hỗ trợ đầy đủ 6 thanh điệu "
        "và các ký tự đặc biệt ă â ê ô ơ ư đ. "
        "Để độ chính xác tốt nhất, cài thêm liblouis: apt install liblouis-dev && pip install louis"
    )
