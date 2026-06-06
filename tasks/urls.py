from django.urls import path
from tasks.views import ProcessInvoiceTaskView

urlpatterns = [
    path('tasks/process-invoice/', ProcessInvoiceTaskView.as_view(), name='task-process-invoice'),
]
