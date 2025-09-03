import re
from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _


class PasswordComplexityValidator:
    """
    密码复杂度验证器
    要求：至少8位，包含字母、数字和特殊字符
    """
    
    def __init__(self, min_length=8):
        self.min_length = min_length
    
    def validate(self, password, user=None):
        if len(password) < self.min_length:
            raise ValidationError(
                _('密码长度至少为%(min_length)d位'),
                code='password_too_short',
                params={'min_length': self.min_length},
            )
        
        # 检查是否包含字母
        if not re.search(r'[a-zA-Z]', password):
            raise ValidationError(
                _('密码必须包含至少一个字母'),
                code='password_no_letters',
            )
        
        # 检查是否包含数字
        if not re.search(r'\d', password):
            raise ValidationError(
                _('密码必须包含至少一个数字'),
                code='password_no_digits',
            )
        
        # 检查是否包含特殊字符
        if not re.search(r'[!@#$%^&*()_+\-=\[\]{};\':"\\|,.<>/?]', password):
            raise ValidationError(
                _('密码必须包含至少一个特殊字符'),
                code='password_no_special_chars',
            )
    
    def get_help_text(self):
        return _(
            '密码必须至少%(min_length)d位，包含字母、数字和特殊字符'
        ) % {'min_length': self.min_length}


def validate_password_complexity(password):
    """
    密码复杂度验证函数
    """
    validator = PasswordComplexityValidator()
    validator.validate(password)