# Spec 05 — RBAC Permissions & Role-Based Access Control

## Goal
Implement custom Django REST Framework permission classes that read role claims from the ephemeral `request.user` object (populated in Spec 03 by `SupabaseJWTAuthentication`). Permissions must never hit the database for role resolution — roles are extracted exclusively from the decoded JWT's `app_metadata`.

---

## Scope
- Create `api/permissions.py` with reusable DRF permission classes
- Cover roles: `customer`, `staff`, `manager`
- Apply permissions at the view level (not globally), so public catalog endpoints remain accessible
- Write unit tests for each permission class

---

## Files to Create / Modify

### `api/permissions.py` — New file

```python
from rest_framework.permissions import BasePermission


class IsManager(BasePermission):
    """
    Grants access only to users with the 'manager' role embedded in their JWT app_metadata.
    Use for: price configuration, user management, financial report endpoints.
    """
    message = "Access restricted to managers."

    def has_permission(self, request, view):
        return (
            request.user
            and request.user.is_authenticated
            and getattr(request.user, 'role', None) == 'manager'
        )


class IsStaffOrManager(BasePermission):
    """
    Grants access to 'staff' or 'manager' roles.
    Use for: barcode generation, invoice upload, stock adjustment endpoints.
    """
    message = "Access restricted to staff and managers."

    def has_permission(self, request, view):
        return (
            request.user
            and request.user.is_authenticated
            and getattr(request.user, 'role', None) in ('staff', 'manager')
        )


class IsOwnerOrStaff(BasePermission):
    """
    Object-level permission: allows access if the requesting user owns the object
    (user_id match) OR is staff/manager.
    Use for: order detail views, reservation status checks.
    """
    message = "You do not have permission to access this resource."

    def has_object_permission(self, request, view, obj):
        role = getattr(request.user, 'role', None)
        if role in ('staff', 'manager'):
            return True
        # Compare Supabase UID from JWT (request.user.username) to object owner
        return str(getattr(obj, 'user_id', None)) == str(request.user.username)
```

---

### `api/permissions.py` — Usage pattern (reference, not code to create)

Apply at the view class level using `permission_classes`:

```python
from api.permissions import IsStaffOrManager

class BarcodeGenerateView(APIView):
    permission_classes = [IsStaffOrManager]
    ...
```

For public read / authenticated write patterns:

```python
from rest_framework.permissions import IsAuthenticatedOrReadOnly

class ProductListView(generics.ListAPIView):
    permission_classes = [IsAuthenticatedOrReadOnly]
    ...
```

---

### `api/tests/test_permissions.py` — Unit Tests

```python
from unittest.mock import MagicMock
from django.test import TestCase
from api.permissions import IsManager, IsStaffOrManager, IsOwnerOrStaff


def make_request(role, uid='user-123'):
    """Helper: build a mock DRF request with a fake authenticated user."""
    user = MagicMock()
    user.is_authenticated = True
    user.role = role
    user.username = uid
    request = MagicMock()
    request.user = user
    return request


class TestIsManager(TestCase):
    def test_manager_role_passes(self):
        self.assertTrue(IsManager().has_permission(make_request('manager'), None))

    def test_staff_role_rejected(self):
        self.assertFalse(IsManager().has_permission(make_request('staff'), None))

    def test_customer_role_rejected(self):
        self.assertFalse(IsManager().has_permission(make_request('customer'), None))


class TestIsStaffOrManager(TestCase):
    def test_staff_passes(self):
        self.assertTrue(IsStaffOrManager().has_permission(make_request('staff'), None))

    def test_manager_passes(self):
        self.assertTrue(IsStaffOrManager().has_permission(make_request('manager'), None))

    def test_customer_rejected(self):
        self.assertFalse(IsStaffOrManager().has_permission(make_request('customer'), None))


class TestIsOwnerOrStaff(TestCase):
    def _make_obj(self, user_id):
        obj = MagicMock()
        obj.user_id = user_id
        return obj

    def test_owner_passes(self):
        perm = IsOwnerOrStaff()
        req = make_request('customer', uid='abc-123')
        obj = self._make_obj('abc-123')
        self.assertTrue(perm.has_object_permission(req, None, obj))

    def test_non_owner_customer_rejected(self):
        perm = IsOwnerOrStaff()
        req = make_request('customer', uid='abc-123')
        obj = self._make_obj('xyz-999')
        self.assertFalse(perm.has_object_permission(req, None, obj))

    def test_staff_can_access_any(self):
        perm = IsOwnerOrStaff()
        req = make_request('staff', uid='abc-123')
        obj = self._make_obj('completely-different-uid')
        self.assertTrue(perm.has_object_permission(req, None, obj))
```

---

## Acceptance Criteria

- [ ] `python manage.py test api.tests.test_permissions` passes with zero failures
- [ ] No permission class makes a database query (verify with `assertNumQueries(0)` in tests)
- [ ] `IsManager`, `IsStaffOrManager`, `IsOwnerOrStaff` are importable from `api.permissions`
- [ ] DRF `DEFAULT_PERMISSION_CLASSES` in settings remains `IsAuthenticated` (global default)
- [ ] `api/permissions.py` contains no hardcoded user IDs or role strings outside class constants
