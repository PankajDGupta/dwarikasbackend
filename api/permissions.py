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
