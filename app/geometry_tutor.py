from __future__ import annotations

from typing import Any


def explain_geometry(question: str, profile: dict[str, Any] | None = None, context: list[str] | None = None, language: str = "en") -> str:
    profile = profile or {}
    context = context or []
    grade = profile.get("grade", "your grade")
    lowered = question.lower()
    context_text = "\n".join(f"- {item}" for item in context[:2])

    if "pythag" in lowered or "pitago" in lowered or "right triangle" in lowered or "tam giác vuông" in lowered:
        concept_en = "The Pythagorean theorem is used in a right triangle. The two shorter sides meet at the right angle. If you square both shorter sides and add them, you get the square of the longest side."
        tactile_en = "Use two rulers to form an L shape on the desk. The side across from the corner is the longest side. That opposite side is called the hypotenuse."
        concept_vi = "Định lý Pythagore dùng cho tam giác vuông. Hai cạnh ngắn gặp nhau tại góc vuông. Nếu bình phương hai cạnh ngắn rồi cộng lại, ta được bình phương cạnh dài nhất."
        tactile_vi = "Em có thể đặt hai chiếc thước thành hình chữ L trên mặt bàn. Cạnh nối hai đầu còn lại là cạnh dài nhất, gọi là cạnh huyền."
    elif "parallel" in lowered or "song song" in lowered:
        concept_en = "Parallel lines are two straight lines that stay the same distance apart and never meet."
        tactile_en = "Feel the two long edges of a ruler or two sides of a notebook. They run in the same direction and do not cross."
        concept_vi = "Hai đường thẳng song song là hai đường luôn cách đều nhau và không bao giờ cắt nhau."
        tactile_vi = "Em hãy sờ hai cạnh dài của thước hoặc hai mép song song của quyển vở. Chúng đi cùng hướng và không cắt nhau."
    else:
        concept_en = "An isosceles triangle is a triangle with two equal sides. Imagine using two sticks of the same length and one shorter stick. Put the two equal sticks so they meet at one point, then connect their open ends with the third stick."
        tactile_en = "Use two equal pens and one shorter pen to make a triangle on your desk."
        concept_vi = "Tam giác cân là tam giác có hai cạnh bằng nhau. Em hãy tưởng tượng có hai que tính dài bằng nhau và một que ngắn hơn. Hai que bằng nhau gặp nhau tại một điểm, hai đầu còn lại được nối bằng que thứ ba."
        tactile_vi = "Em có thể dùng hai chiếc bút bằng nhau và một chiếc bút ngắn hơn để xếp thành tam giác trên bàn."

    if language == "vi":
        return (
            f"Cô sẽ giải thích theo cách phù hợp với học sinh {grade}, dùng lời nói và ví dụ có thể sờ/chạm.\n\n"
            f"{concept_vi}\n\n"
            "Bước 1: Một tam giác có ba cạnh.\n"
            "Bước 2: Gọi tên từng cạnh và từng góc thật chậm.\n"
            "Bước 3: Dùng tay hoặc trí tưởng tượng để nhận ra quan hệ quan trọng: bằng nhau, song song, vuông góc, trung điểm hoặc cạnh dài nhất.\n"
            "Bước 4: Nói lại kết luận bằng một câu ngắn.\n\n"
            f"Ví dụ xúc giác: {tactile_vi}\n\n"
            "Câu hỏi kiểm tra nhanh: Em hãy nói cạnh, góc hoặc điểm nào là quan trọng nhất trong bài này.\n\n"
            f"Kiến thức đã dùng:\n{context_text if context_text else '- Quy tắc hình học cơ bản'}"
        )

    return (
        f"I will explain this for a student in {grade} using words and touch-based examples.\n\n"
        f"{concept_en}\n\n"
        "Step 1: A triangle has three sides.\n"
        "Step 2: Name the sides and angles slowly, one by one.\n"
        "Step 3: Touch or imagine the key relationship: equal, parallel, perpendicular, midpoint, or longest side.\n"
        "Step 4: Say the conclusion aloud in one sentence.\n\n"
        f"Touch-based practice: {tactile_en}\n\n"
        "Practice:\n"
        "1. Describe the shape aloud using point names.\n"
        "2. Name two equal, parallel, or perpendicular parts if they exist.\n"
        "3. Explain the conclusion in one short sentence.\n\n"
        f"Knowledge used:\n{context_text if context_text else '- Basic geometry rule'}"
    )

