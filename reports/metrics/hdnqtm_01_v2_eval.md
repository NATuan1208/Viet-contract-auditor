# E2E Evaluation Report

## Inputs
- Report: reports\final_outputs\hdnqtm_01_v2_report.md
- Groundtruth: result-example\HDNQTM01\groundtruth_hdnqtm_01.json

## Metrics
- Predicted violations: 4
- Groundtruth vulnerabilities: 3
- Precision (heuristic): 0.750
- Recall (heuristic): 1.000
- F1 (heuristic): 0.857

## Groundtruth Coverage
- GT#1 [Omission] [Phần xét thấy / Điều 1] -> MATCH PRED#4 (score=0.549, loc=0.950, sem=0.212, type=0.000) | Không nêu rõ hệ thống nhượng quyền đã hoạt động ít nhất 01 năm trước khi nhượng quyền.
- GT#2 [Logical] [Điều 4.2.14] -> MATCH PRED#1 (score=0.596, loc=1.000, sem=0.274, type=0.000) | Cấm bên nhận quyền khiếu nại về chất lượng thiết bị do bên nhượng cung cấp.
- GT#3 [Numeric] [Điều 6.1.2] -> MATCH PRED#2 (score=0.747, loc=1.000, sem=0.276, type=1.000) | Phí nhượng quyền định kỳ tự động tăng 20% mỗi năm mà không cần thỏa thuận.

## Notes
- Đây là so sánh heuristic theo token overlap, không phải legal judgment tuyệt đối.
- Nếu cần đánh giá sát nghĩa hơn, có thể thêm LLM-as-judge ở Phase 6.