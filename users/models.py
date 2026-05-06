from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models


class UserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError('Email is required')
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password, **extra_fields):
        extra_fields.setdefault('is_admin', True)
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        return self.create_user(email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    email = models.EmailField(unique=True)
    display_name = models.CharField(max_length=200, blank=True)
    department = models.CharField(max_length=200, blank=True)
    entra_id = models.CharField(max_length=100, blank=True, db_index=True)

    is_active = models.BooleanField(default=True)
    is_admin = models.BooleanField(default=False)
    is_it_manager = models.BooleanField(default=False)
    # is_staff controls Django admin access
    is_staff = models.BooleanField(default=False)

    # Notification preferences
    notify_on_assign = models.BooleanField(default=True)
    notify_on_update = models.BooleanField(default=True)
    notify_on_sla_breach = models.BooleanField(default=True)

    ticket_list_filter = models.TextField(blank=True, default='')

    last_sync = models.DateTimeField(null=True, blank=True)
    date_joined = models.DateTimeField(auto_now_add=True)

    objects = UserManager()

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []

    class Meta:
        ordering = ['display_name', 'email']

    def __str__(self):
        return self.display_name or self.email

    @property
    def full_name(self):
        return self.display_name or self.email
