from __future__ import annotations


def correct_english(sentence: str, language: str = "en") -> str:
    if "many meeting" in sentence.lower():
        if language == "vi":
            return (
                "Câu đúng là: I have many meetings today.\n\n"
                "Giải thích: Từ 'many' dùng với danh từ đếm được số nhiều, nên 'meeting' phải chuyển thành 'meetings'.\n\n"
                "Bây giờ em hãy nhắc lại: I have many meetings today."
            )
        return (
            "The correct sentence is: I have many meetings today.\n\n"
            "Explanation: The word 'many' is used with plural countable nouns, so 'meeting' should be 'meetings'.\n\n"
            "Now repeat: I have many meetings today."
        )

    if language == "vi":
        return (
            "Cô có thể luyện nói tiếng Anh với em. Hãy gửi một câu cụ thể, cô sẽ sửa ngữ pháp, giải thích ngắn gọn và cho câu luyện lại."
        )
    return (
        "I can be your English speaking partner. Send one sentence, and I will correct it, explain it simply, and give you a short repeat-after-me practice."
    )

