# E2E Evaluation Report

## Inputs
- Report: HDLD_ThucHanh_01_report_e2e_gt1.md
- Groundtruth: result-example\groundtruth_hdld_01_test copy.json

## Metrics
- Predicted violations: 11
- Groundtruth vulnerabilities: 5
- Precision (heuristic): 0.455
- Recall (heuristic): 1.000
- F1 (heuristic): 0.625

## Groundtruth Coverage
- GT#1 [Numeric] [Điều 3.1(ix)] -> MATCH PRED#3 (score=0.470, loc=0.550, sem=0.128, type=1.000) | Sửa cách tính tiền lương làm thêm giờ thành 100% tiền lương giờ thực trả trong mọi trường hợp.
- GT#2 [Numeric] [Điều 3.2(vi)] -> MATCH PRED#4 (score=0.397, loc=0.550, sem=0.133, type=0.500) | Rút thời hạn báo trước khi người lao động đơn phương chấm dứt hợp đồng xuống tối thiểu 03 ngày làm việc trong mọi trường hợp.
- GT#3 [Logical] [Điều 4.1(ii)] -> MATCH PRED#6 (score=0.402, loc=0.550, sem=0.149, type=0.500) | Thêm quyền đơn phương điều chỉnh lương, phụ cấp và chế độ liên quan của công ty mà không cần sự chấp thuận trước của người lao động.
- GT#4 [Logical] [Điều 5.1] -> MATCH PRED#8 (score=0.405, loc=0.550, sem=0.157, type=0.500) | Quy định mọi thay đổi do Công ty thông báo bằng văn bản sẽ tự động có hiệu lực nếu người lao động không phản đối trong 03 ngày làm việc.
- GT#5 [Omission] [Điều 2] -> MATCH PRED#2 (score=0.537, loc=1.000, sem=0.107, type=0.000) | Lược bỏ hoàn toàn mục 2.4 về điều kiện an toàn và vệ sinh lao động tại nơi làm việc.

## Notes
- Đây là so sánh heuristic theo token overlap, không phải legal judgment tuyệt đối.
- Nếu cần đánh giá sát nghĩa hơn, có thể thêm LLM-as-judge ở Phase 6.