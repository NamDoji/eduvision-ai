# EduVision AI Knowledge Base

This knowledge base uses synthetic demo content for conference presentation. It is not copied from textbooks and does not contain real student personal data.

## Accessible Geometry Principles

For visually impaired or low-vision students, geometry explanations should be verbal, tactile, and step by step. Avoid visual-only wording such as "look at the picture" or "as shown in the figure". Prefer "imagine", "touch", "feel", "use two sticks", "use string", "use cardboard", "use the desk edge", or "use a raised-line drawing".

When describing a geometry problem, name the objects in this order: points, segments, equal lengths, angles, parallel or perpendicular lines, and the conclusion to prove. If the problem contains a diagram, explain the relationship verbally before giving the solution.

## Isosceles Triangle

An isosceles triangle has two equal sides. A tactile example is two equal sticks and one shorter stick. The two equal sticks meet at one point, and their open ends are connected by the third stick. The angles opposite the two equal sides are equal.

Demo explanation: "Imagine triangle ABC. Side AB and side AC have the same length. If you touch two equal sticks meeting at point A, the open ends are B and C. The side BC connects them. Because AB equals AC, the angles at B and C are equal."

## Right Triangle And Pythagorean Theorem

A right triangle has one right angle, like the corner of a book or desk. The side opposite the right angle is the longest side, called the hypotenuse. The Pythagorean theorem says: in a right triangle, the square of one shorter side plus the square of the other shorter side equals the square of the longest side.

Accessible explanation: "Use two rulers to form an L shape. The diagonal side connecting the open ends is the longest side. The formula describes how the lengths of those three sides relate."

## Định Lý Pythagore Cho Học Sinh Nhìn Mờ

Định lý Pythagore dùng cho tam giác vuông. Hai cạnh ngắn gặp nhau tại góc vuông, giống như hai cạnh của một góc bàn hoặc góc quyển sách. Cạnh đối diện góc vuông là cạnh dài nhất, gọi là cạnh huyền. Công thức nói rằng: bình phương cạnh góc vuông thứ nhất cộng bình phương cạnh góc vuông thứ hai bằng bình phương cạnh huyền.

Giải thích xúc giác: "Em đặt hai chiếc thước thành hình chữ L trên mặt bàn. Đoạn nối hai đầu còn lại là cạnh huyền. Định lý Pythagore mô tả quan hệ độ dài giữa ba cạnh đó."

## Parallel And Perpendicular Lines

Parallel lines run in the same direction and never meet. The two long edges of a ruler are parallel. Perpendicular lines meet at a right angle. The corner of a desk or book is a tactile example of perpendicular lines.

## Median And Midpoint

A midpoint divides a segment into two equal parts. A median of a triangle is a segment from one vertex to the midpoint of the opposite side. A student can fold a string in half to feel a midpoint, then place another string from a triangle corner to that midpoint.

## Reading Tables And Charts

For a table, read the title first, then column names, then each row. After reading values, summarize the highest value, lowest value, pattern, or comparison. For a chart, read the title, axis labels, unit, categories, values, trend, and conclusion.

Example: "The table is titled Weekly Scores. It has two columns: Day and Score. Monday is 6, Tuesday is 7, Wednesday is 8. The score increases from Monday to Wednesday."

## English Correction Pattern

When correcting English, provide:

1. Correct sentence.
2. Short reason.
3. Two or three similar examples.
4. One practice question.

Example: "I have many meeting today" becomes "I have many meetings today." The word "many" needs a plural countable noun.

## Study Planning Pattern

A useful plan for a low-vision learner should be short, repeatable, and confidence-building. A 25-minute daily lesson can use:

1. 5 minutes reviewing yesterday's concept.
2. 10 minutes learning one new idea through verbal and tactile examples.
3. 7 minutes doing short exercises.
4. 3 minutes summarizing the lesson aloud.

## Student Profile Fields

The student profile should store student_id, name, grade, vision_status, math_level, english_level, weaknesses, strengths, learning_goal, learning_history, and last_activity. For demos, use synthetic data such as S001 instead of real student personal information.

## Safety And Privacy

Student-facing sessions should not receive broad filesystem, shell, email, calendar, or personal account access. The learning assistant should call only approved internal APIs. Logs should avoid sensitive personal data. Unsafe content, abuse indicators, legal or medical questions, and non-learning requests should be escalated to teacher or parent review.

## Conference Demo Script

Demo 1: Ask "/geometry I do not understand an isosceles triangle". Show that the answer avoids "look at the figure" and uses sticks.

Demo 2: Ask "/english I have many meeting today". Show correction, reason, and practice.

Demo 3: Upload a PDF or image with "AB = AC". Show OCR extraction and accessible description.

Demo 4: Ask "/plan I am in Grade 8, weak at geometry, and can study 25 minutes per day". Show weekly plan and progress tracking.
