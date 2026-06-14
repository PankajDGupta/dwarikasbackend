from django.urls import path
from tasks.views import ProcessInvoiceTaskView, RunDiscountAnalysisView

urlpatterns = [
    path('tasks/process-invoice/', ProcessInvoiceTaskView.as_view(), name='task-process-invoice'),
    path('tasks/run-discount-analysis/', RunDiscountAnalysisView.as_view(), name='task-run-discount-analysis'),
]

