# E2E Evaluation Report

## Inputs
- Report: reports\final_outputs\hdbds_01_report.md
- Groundtruth: result-example\HDBDS\groundtruth_hdmbcc_01.json

## Metrics
- Predicted violations: 2
- Groundtruth vulnerabilities: 3
- Precision (heuristic): 0.500
- Recall (heuristic): 0.333
- F1 (heuristic): 0.400

## Groundtruth Coverage
- GT#1 [Omission] [Điều 1 khoản 7 và Điều 2 khoản 1(b)] -> MISS (score=0.143, loc=0.000, sem=0.410, type=0.000) | Không ghi rõ diện tích sử dụng theo kích thước thông thủy, đồng thời loại bỏ cơ chế xác định lại diện tích thông thủy khi cấp Giấy chứng nhận.
- GT#2 [Logical] [Điều 11 khoản 2 và 3] -> MATCH PRED#2 (score=0.597, loc=0.950, sem=0.135, type=0.500) | Quy định phần sân thượng thuộc sở hữu riêng của Chủ đầu tư thay vì là phần sở hữu chung.
- GT#3 [Numeric] [Điều 11 khoản 5] -> MISS (score=0.051, loc=0.000, sem=0.145, type=0.000) | Cho phép phí quản lý vận hành tăng tùy ý theo quyết định của Ban quản lý mà không có giới hạn hoặc cơ chế kiểm soát.

## Notes
- Đây là so sánh heuristic theo token overlap, không phải legal judgment tuyệt đối.
- Nếu cần đánh giá sát nghĩa hơn, có thể thêm LLM-as-judge ở Phase 6.