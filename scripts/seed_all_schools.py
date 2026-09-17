#!/usr/bin/env python3
"""Seed toàn bộ 5 trường khiếm thị — tạo schools + teachers + students."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.auth import (
    init_auth_db, create_school, create_student_account,
    create_teacher_account, reset_user_password, list_school_users,
)
from app.main import init_db, save_profile

# ── CẤU HÌNH 5 TRƯỜNG ────────────────────────────────────────────────────────
SCHOOLS = [
    {"code": "ndc", "name": "Trường Nguyễn Đình Chiểu", "city": "Hà Nội",       "count": 200, "grades": range(6, 13)},
    {"code": "tb",  "name": "Trường Khiếm Thị Thái Bình","city": "Thái Bình",    "count": 200, "grades": range(6, 13)},
    {"code": "bn",  "name": "Trường Khiếm Thị Bắc Ninh", "city": "Bắc Ninh",    "count": 150, "grades": range(6, 13)},
    {"code": "hy",  "name": "Trường Khiếm Thị Hưng Yên", "city": "Hưng Yên",    "count": 150, "grades": range(6, 13)},
    {"code": "hp",  "name": "Trường Khiếm Thị Hải Phòng","city": "Hải Phòng",   "count": 200, "grades": range(6, 13)},
]

TEACHER_DISPLAY = {
    "ndc": "Giáo viên NDC", "tb": "Giáo viên Thái Bình",
    "bn": "Giáo viên Bắc Ninh", "hy": "Giáo viên Hưng Yên",
    "hp": "Giáo viên Hải Phòng",
}

# ── HỌ TÊN PHONG PHÚ ─────────────────────────────────────────────────────────
HO = (["Nguyễn"] * 14 + ["Trần"] * 10 + ["Lê"] * 8 + ["Phạm"] * 7
      + ["Hoàng"] * 6 + ["Phan"] * 5 + ["Vũ"] * 5 + ["Đặng"] * 4
      + ["Bùi"] * 4 + ["Đỗ"] * 3 + ["Hồ"] * 2 + ["Ngô"] * 2)

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
]

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
]

WEAKNESSES_BY_GRADE = {
    6: ["số học cơ bản", "hình học phẳng"],
    7: ["đại số sơ cấp", "hình học phẳng"],
    8: ["đại số", "hình học"],
    9: ["đại số nâng cao", "hình học không gian"],
    10: ["đại số", "giải tích"],
    11: ["giải tích", "hình học không gian"],
    12: ["giải tích nâng cao", "xác suất thống kê"],
}
ENGLISH_LEVEL = {6: "A1", 7: "A1", 8: "A2", 9: "A2", 10: "B1", 11: "B1", 12: "B1"}
MATH_LEVEL = {6: "yếu", 7: "yếu", 8: "trung bình", 9: "trung bình", 10: "khá", 11: "khá", 12: "khá"}


def make_profile(school: dict, i: int, username: str, student_id: str, display_name: str) -> dict:
    count = school["count"]
    grades = list(school["grades"])
    per_grade = max(count // len(grades), 1)
    grade_num = grades[min((i - 1) // per_grade, len(grades) - 1)]
    vision = "blind" if i <= int(count * 0.6) else "low vision"
    return {
        "student_id": student_id,
        "name": display_name,
        "grade": f"Lớp {grade_num}",
        "vision_status": vision,
        "math_level": MATH_LEVEL.get(grade_num, "trung bình"),
        "english_level": ENGLISH_LEVEL.get(grade_num, "A1"),
        "weaknesses": WEAKNESSES_BY_GRADE.get(grade_num, ["hình học"]),
        "strengths": ["nghe hiểu bài giảng", "ghi nhớ công thức"],
        "learning_goal": f"Hoàn thành chương trình Lớp {grade_num} và thi đạt kết quả tốt",
        "school": f"{school['name']} - {school['city']}",
    }


def seed_school(school: dict, existing_usernames: set) -> tuple:
    code = school["code"]
    count = school["count"]
    created = 0
    skipped = 0

    for i in range(1, count + 1):
        n = f"{i:03d}"
        username = f"{code}{n}"
        student_id = f"{code.upper()}{n}"

        is_male = (i % 2 == 1)
        idx = (i - 1) // 2 if is_male else (i // 2 - 1)
        ho = HO[idx % len(HO)]
        ten = (TEN_NAM if is_male else TEN_NU)[idx % len(TEN_NAM if is_male else TEN_NU)]
        display_name = f"{ho} {ten}"

        if username in existing_usernames:
            skipped += 1
            continue

        try:
            create_student_account(username, "1", student_id, display_name, school_code=code)
            profile = make_profile(school, i, username, student_id, display_name)
            save_profile(profile)
            existing_usernames.add(username)
            created += 1
            if i % 50 == 0:
                print(f"    {i}/{count} {username} {display_name}")
        except ValueError:
            skipped += 1

    return created, skipped


def main() -> None:
    init_db()
    init_auth_db()

    print("\n=== SEED 5 TRƯỜNG KHIẾM THỊ ===\n")

    # Track existing to avoid duplicates
    existing = {u["username"] for u in __import__("app.auth", fromlist=["list_users"]).list_users()}

    total_created = 0
    total_skipped = 0

    for school in SCHOOLS:
        code = school["code"]
        print(f"[{code.upper()}] {school['name']} — {school['city']} ({school['count']} HS)")

        # Tạo trường
        create_school(code, school["name"], school["city"])

        # Tạo giáo viên
        teacher_user = f"{code}_giaovien"
        if teacher_user not in existing:
            try:
                create_teacher_account(teacher_user, "1", code, TEACHER_DISPLAY[code])
                existing.add(teacher_user)
                print(f"    GV: {teacher_user} ✅")
            except ValueError:
                print(f"    GV: {teacher_user} (đã tồn tại)")
        else:
            print(f"    GV: {teacher_user} (đã tồn tại)")

        # Tạo học sinh
        created, skipped = seed_school(school, existing)
        total_created += created
        total_skipped += skipped
        print(f"    HS: {created} tạo mới, {skipped} bỏ qua\n")

    print(f"=== HOÀN TẤT ===")
    print(f"Tổng tạo mới: {total_created} HS")
    print(f"Tổng bỏ qua:  {total_skipped}")
    print(f"\nTài khoản truy cập:")
    for s in SCHOOLS:
        print(f"  GV  {s['code']}_giaovien / 1  ({s['name']})")
    print(f"  ADMIN  admin / admin2026  (Quản trị tổng)")
    print(f"\n  HS: {{mã_trường}}001 – {{mã_trường}}{{count}} / pass: 1")
    print(f"      VD: ndc001, tb001, bn001, hy001, hp001")


if __name__ == "__main__":
    main()
