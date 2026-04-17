# E2E Evaluation Report

## Inputs
- Report: reports\final_outputs\hdnqtm_01_report.md
- Groundtruth: D:\SinhVien\UIT_HocChinhKhoa\HK2 2025 - 2026\CS 431\Viet-contract-auditor\result-example\HDNQTM01\groundtruth_hdnqtm_01.json

## Metrics
- Predicted violations: 3
- Groundtruth vulnerabilities: 3
- Precision (heuristic): 1.000
- Recall (heuristic): 1.000
- F1 (heuristic): 1.000

## Groundtruth Coverage
- GT#1 [Omission] [Phần xét thấy / Điều 1] -> MATCH PRED#3 (score=0.552, loc=0.950, sem=0.219, type=0.000) | Không nêu rõ hệ thống nhượng quyền đã hoạt động ít nhất 01 năm trước khi nhượng quyền.
- GT#2 [Logical] [Điều 4.2.14] -> MATCH PRED#1 (score=0.574, loc=1.000, sem=0.212, type=0.000) | Cấm bên nhận quyền khiếu nại về chất lượng thiết bị do bên nhượng cung cấp.
- GT#3 [Numeric] [Điều 6.1.2] -> MATCH PRED#2 (score=0.755, loc=1.000, sem=0.300, type=1.000) | Phí nhượng quyền định kỳ tự động tăng 20% mỗi năm mà không cần thỏa thuận.

## Notes
- Đây là so sánh heuristic theo token overlap, không phải legal judgment tuyệt đối.
- Nếu cần đánh giá sát nghĩa hơn, có thể thêm LLM-as-judge ở Phase 6.