#!/usr/bin/env python3
"""Seed 200 student accounts for Trường PTCS Nguyễn Đình Chiểu - Hà Nội.

Cấu trúc:
  - username: ndc001 ... ndc200
  - student_id: NDC001 ... NDC200
  - password mặc định: "1"
  - Lớp 6–12 (khoảng 28–29 HS mỗi lớp)
  - Xen kẽ nam/nữ, 60% mù hoàn toàn / 40% nhìn kém
"""
import sys
from pathlib import Path

# Đảm bảo import được app package
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.auth import init_auth_db, create_student_account
from app.main import init_db, save_profile

# ── HỌ PHỔ BIẾN ──────────────────────────────────────────────────────────────
HO_NAM = (
    ["Nguyễn"] * 14 + ["Trần"] * 10 + ["Lê"] * 8 + ["Phạm"] * 7
    + ["Hoàng"] * 6 + ["Phan"] * 5 + ["Vũ"] * 5 + ["Đặng"] * 4
    + ["Bùi"] * 4 + ["Đỗ"] * 3 + ["Hồ"] * 2 + ["Ngô"] * 2
)  # 70 items

HO_NU = (
    ["Nguyễn"] * 14 + ["Trần"] * 10 + ["Lê"] * 8 + ["Phạm"] * 7
    + ["Hoàng"] * 6 + ["Phan"] * 5 + ["Vũ"] * 5 + ["Đặng"] * 4
    + ["Bùi"] * 4 + ["Đỗ"] * 3 + ["Hồ"] * 2 + ["Ngô"] * 2
)

TEN_NAM = [
    "Văn An", "Đức Hùng", "Quang Dũng", "Tiến Tú", "Hoàng Nam",
    "Bảo Long", "Minh Khôi", "Gia Huy", "Trọng Khang", "Công Hiếu",
    "Hữu Quân", "Xuân Hải", "Thành Cường", "Phúc Bình", "Đình Thắng",
    "Thanh Phong", "Chí Tuấn", "Khắc Kiên", "Vĩnh Đại", "Anh Lâm",
    "Đức Sơn", "Quang Thịnh", "Minh Vinh", "Bảo Khánh", "Gia Hạo",
    "Văn Hùng", "Đức Dũng", "Quang Tú", "Tiến Nam", "Hoàng Long",
    "Minh An", "Gia Khôi", "Trọng Huy", "Công Khang", "Hữu Hiếu",
    "Xuân Quân", "Thành Hải", "Phúc Cường", "Đình Bình", "Thanh Thắng",
    "Chí Phong", "Khắc Tuấn", "Vĩnh Kiên", "Anh Đại", "Đức Lâm",
    "Quang Sơn", "Minh Thịnh", "Bảo Vinh", "Gia Khánh", "Văn Hạo",
    "Đức An", "Minh Hùng", "Bảo Dũng", "Gia Tú", "Trọng Nam",
    "Công Long", "Hữu Khôi", "Xuân Huy", "Thành Khang", "Phúc Hiếu",
    "Đình Quân", "Thanh Hải", "Chí Cường", "Khắc Bình", "Vĩnh Thắng",
    "Anh Phong", "Đức Tuấn", "Quang Kiên", "Minh Đại", "Bảo Lâm",
    "Gia Sơn", "Trọng Thịnh", "Công Vinh", "Hữu Khánh", "Xuân Hạo",
    "Thành An", "Phúc Hùng", "Đình Dũng", "Thanh Tú", "Chí Nam",
    "Khắc Long", "Vĩnh Khôi", "Anh Huy", "Đức Khang", "Quang Hiếu",
    "Minh Quân", "Bảo Hải", "Gia Cường", "Trọng Bình", "Công Thắng",
    "Hữu Phong", "Xuân Tuấn", "Thành Kiên", "Phúc Đại", "Đình Lâm",
    "Thanh Sơn", "Chí Thịnh", "Khắc Vinh", "Vĩnh Khánh", "Anh Hạo",
]  # 100 tên nam

TEN_NU = [
    "Thị Hoa", "Ngọc Linh", "Thúy Ánh", "Bích Ngân", "Thu Hương",
    "Phương Thảo", "Minh Châu", "Lan Anh", "Hồng Nhung", "Diệu Oanh",
    "Kim Thư", "Mỹ Mai", "Thanh Yến", "Thùy Trang", "Xuân Hằng",
    "Bảo Hà", "Quỳnh Trinh", "Mai Nhi", "Thị Vân", "Ngọc Hân",
    "Thúy Ly", "Bích Ngà", "Thu Thanh", "Phương Chi", "Minh Hiền",
    "Lan Hoa", "Hồng Linh", "Diệu Ánh", "Kim Ngân", "Mỹ Hương",
    "Thanh Thảo", "Thùy Châu", "Xuân Anh", "Bảo Nhung", "Quỳnh Oanh",
    "Mai Thư", "Thị Mai", "Ngọc Yến", "Thúy Trang", "Bích Hằng",
    "Thu Hà", "Phương Trinh", "Minh Nhi", "Lan Vân", "Hồng Hân",
    "Diệu Ly", "Kim Ngà", "Mỹ Thanh", "Thanh Chi", "Thùy Hiền",
    "Xuân Hoa", "Bảo Linh", "Quỳnh Ánh", "Mai Ngân", "Thị Hương",
    "Ngọc Thảo", "Thúy Châu", "Bích Anh", "Thu Nhung", "Phương Oanh",
    "Minh Thư", "Lan Mai", "Hồng Yến", "Diệu Trang", "Kim Hằng",
    "Mỹ Hà", "Thanh Trinh", "Thùy Nhi", "Xuân Vân", "Bảo Hân",
    "Quỳnh Ly", "Mai Ngà", "Thị Thanh", "Ngọc Chi", "Thúy Hiền",
    "Bích Hoa", "Thu Linh", "Phương Ánh", "Minh Ngân", "Lan Hương",
    "Hồng Thảo", "Diệu Châu", "Kim Anh", "Mỹ Nhung", "Thanh Oanh",
    "Thùy Thư", "Xuân Mai", "Bảo Yến", "Quỳnh Trang", "Mai Hằng",
    "Thị Hà", "Ngọc Trinh", "Thúy Nhi", "Bích Vân", "Thu Hân",
    "Phương Ly", "Minh Ngà", "Lan Thanh", "Hồng Chi", "Diệu Hiền",
]  # 100 tên nữ

# Môn học yếu theo lớp (để build profile phong phú hơn)
WEAKNESSES_BY_GRADE = {
    6: ["số học cơ bản", "hình học phẳng"],
    7: ["đại số sơ cấp", "hình học phẳng"],
    8: ["đại số", "hình học"],
    9: ["đại số nâng cao", "hình học không gian"],
    10: ["đại số", "hình giải tích"],
    11: ["giải tích", "hình học không gian"],
    12: ["giải tích nâng cao", "xác suất thống kê"],
}

STRENGTHS = ["nghe hiểu bài giảng", "ghi nhớ công thức", "giải bài miệng"]

ENGLISH_BY_GRADE = {6: "A1", 7: "A1", 8: "A2", 9: "A2", 10: "B1", 11: "B1", 12: "B1"}
MATH_BY_GRADE = {6: "yếu", 7: "yếu", 8: "trung bình", 9: "trung bình", 10: "khá", 11: "khá", 12: "khá"}


def main() -> None:
    init_db()
    init_auth_db()

    created = 0
    skipped = 0

    for i in range(1, 201):
        n = f"{i:03d}"
        username = f"ndc{n}"
        student_id = f"NDC{n}"

        # Xen kẽ nam (lẻ) / nữ (chẵn)
        is_male = (i % 2 == 1)
        idx = (i - 1) // 2 if is_male else (i // 2 - 1)
        if is_male:
            ho = HO_NAM[idx % len(HO_NAM)]
            ten = TEN_NAM[idx % len(TEN_NAM)]
        else:
            ho = HO_NU[idx % len(HO_NU)]
            ten = TEN_NU[idx % len(TEN_NU)]

        display_name = f"{ho} {ten}"

        # Phân bổ lớp 6–12 (~28–29 HS/lớp)
        grade_idx = (i - 1) // 29       # 0–6
        grade_num = min(grade_idx + 6, 12)
        grade_str = f"Lớp {grade_num}"

        # Trạng thái thị giác: 60% mù hoàn toàn, 40% nhìn kém
        vision = "blind" if i <= 120 else "low vision"

        profile = {
            "student_id": student_id,
            "name": display_name,
            "grade": grade_str,
            "vision_status": vision,
            "math_level": MATH_BY_GRADE.get(grade_num, "trung bình"),
            "english_level": ENGLISH_BY_GRADE.get(grade_num, "A1"),
            "weaknesses": WEAKNESSES_BY_GRADE.get(grade_num, ["hình học"]),
            "strengths": STRENGTHS,
            "learning_goal": f"Hoàn thành chương trình {grade_str} và thi đạt kết quả tốt",
            "school": "Trường PTCS Nguyễn Đình Chiểu - Hà Nội",
        }

        try:
            create_student_account(username, "1", student_id, display_name)
            save_profile(profile)
            created += 1
            if i % 25 == 0 or i == 1:
                print(f"  ✅ {i:3d}/200  {username}  {display_name}  ({grade_str}, {vision})")
        except ValueError:
            skipped += 1

    print(f"\n🎉 Hoàn tất: tạo mới {created} tài khoản, bỏ qua {skipped} (đã tồn tại)")
    print(f"   Username: ndc001 – ndc200 | Pass mặc định: 1")
    print(f"   Trường: Nguyễn Đình Chiểu - Hà Nội | Lớp 6–12")


if __name__ == "__main__":
    main()
