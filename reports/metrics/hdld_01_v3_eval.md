# E2E Evaluation Report

## Inputs
- Report: reports\final_outputs\hdld_01_v3_report.md
- Groundtruth: result-example\HDLD\groundtruth_hdld_01_test copy.json

## Metrics
- Predicted violations: 3
- Groundtruth vulnerabilities: 5
- Precision (heuristic): 1.000
- Recall (heuristic): 0.600
- F1 (heuristic): 0.750

## Groundtruth Coverage
- GT#1 [Numeric] [Điều 3.1(ix)] -> MATCH PRED#3 (score=0.504, loc=0.550, sem=0.225, type=1.000) | Sửa cách tính tiền lương làm thêm giờ thành 100% tiền lương giờ thực trả trong mọi trường hợp.
- GT#2 [Numeric] [Điều 3.2(vi)] -> MISS (score=0.205, loc=0.000, sem=0.156, type=1.000) | Rút thời hạn báo trước khi người lao động đơn phương chấm dứt hợp đồng xuống tối thiểu 03 ngày làm việc trong mọi trường hợp.
- GT#3 [Logical] [Điều 4.1(ii)] -> MISS (score=0.224, loc=0.000, sem=0.211, type=1.000) | Thêm quyền đơn phương điều chỉnh lương, phụ cấp và chế độ liên quan của công ty mà không cần sự chấp thuận trước của người lao động.
- GT#4 [Logical] [Điều 5.1] -> MATCH PRED#1 (score=0.552, loc=0.550, sem=0.364, type=1.000) | Quy định mọi thay đổi do Công ty thông báo bằng văn bản sẽ tự động có hiệu lực nếu người lao động không phản đối trong 03 ngày làm việc.
- GT#5 [Omission] [Điều 2] -> MATCH PRED#2 (score=0.606, loc=1.000, sem=0.090, type=0.500) | Lược bỏ hoàn toàn mục 2.4 về điều kiện an toàn và vệ sinh lao động tại nơi làm việc.

## Notes
- Đây là so sánh heuristic theo token overlap, không phải legal judgment tuyệt đối.
- Nếu cần đánh giá sát nghĩa hơn, có thể thêm LLM-as-judge ở Phase 6.