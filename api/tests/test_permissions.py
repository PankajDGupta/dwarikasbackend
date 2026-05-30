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


def make_anonymous_request():
    """Helper: build a mock DRF request with an unauthenticated user."""
    user = MagicMock()
    user.is_authenticated = False
    user.role = 'customer'
    request = MagicMock()
    request.user = user
    return request


def make_no_user_request():
    """Helper: build a mock DRF request with no user attached."""
    request = MagicMock()
    request.user = None
    return request


class TestIsManager(TestCase):
    def test_manager_role_passes(self):
        with self.assertNumQueries(0):
            self.assertTrue(IsManager().has_permission(make_request('manager'), None))

    def test_staff_role_rejected(self):
        with self.assertNumQueries(0):
            self.assertFalse(IsManager().has_permission(make_request('staff'), None))

    def test_customer_role_rejected(self):
        with self.assertNumQueries(0):
            self.assertFalse(IsManager().has_permission(make_request('customer'), None))

    def test_anonymous_rejected(self):
        with self.assertNumQueries(0):
            self.assertFalse(IsManager().has_permission(make_anonymous_request(), None))

    def test_no_user_rejected(self):
        with self.assertNumQueries(0):
            self.assertFalse(IsManager().has_permission(make_no_user_request(), None))


class TestIsStaffOrManager(TestCase):
    def test_staff_passes(self):
        with self.assertNumQueries(0):
            self.assertTrue(IsStaffOrManager().has_permission(make_request('staff'), None))

    def test_manager_passes(self):
        with self.assertNumQueries(0):
            self.assertTrue(IsStaffOrManager().has_permission(make_request('manager'), None))

    def test_customer_rejected(self):
        with self.assertNumQueries(0):
            self.assertFalse(IsStaffOrManager().has_permission(make_request('customer'), None))

    def test_anonymous_rejected(self):
        with self.assertNumQueries(0):
            self.assertFalse(IsStaffOrManager().has_permission(make_anonymous_request(), None))

    def test_no_user_rejected(self):
        with self.assertNumQueries(0):
            self.assertFalse(IsStaffOrManager().has_permission(make_no_user_request(), None))


class TestIsOwnerOrStaff(TestCase):
    def _make_obj(self, user_id):
        obj = MagicMock()
        obj.user_id = user_id
        return obj

    def test_owner_passes(self):
        perm = IsOwnerOrStaff()
        req = make_request('customer', uid='abc-123')
        obj = self._make_obj('abc-123')
        with self.assertNumQueries(0):
            self.assertTrue(perm.has_object_permission(req, None, obj))

    def test_non_owner_customer_rejected(self):
        perm = IsOwnerOrStaff()
        req = make_request('customer', uid='abc-123')
        obj = self._make_obj('xyz-999')
        with self.assertNumQueries(0):
            self.assertFalse(perm.has_object_permission(req, None, obj))

    def test_staff_can_access_any(self):
        perm = IsOwnerOrStaff()
        req = make_request('staff', uid='abc-123')
        obj = self._make_obj('completely-different-uid')
        with self.assertNumQueries(0):
            self.assertTrue(perm.has_object_permission(req, None, obj))

    def test_manager_can_access_any(self):
        perm = IsOwnerOrStaff()
        req = make_request('manager', uid='abc-123')
        obj = self._make_obj('completely-different-uid')
        with self.assertNumQueries(0):
            self.assertTrue(perm.has_object_permission(req, None, obj))
