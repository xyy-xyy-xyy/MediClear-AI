# -*- coding: utf-8 -*-
"""引擎统一异常体系。

所有对外抛出的异常都继承 :class:`MediClearError`，
调用方只需捕获一个基类即可兜底。
"""


class MediClearError(Exception):
    """引擎异常基类。"""


class PolicyNotFoundError(MediClearError, ValueError):
    """请求的城市 / 险种 / 就医类型政策不存在。

    同时继承 :class:`ValueError`，兼容历史代码 ``except ValueError`` 的写法。
    """

    def __init__(self, message, *, city=None, insurance_type=None, visit_type=None):
        super().__init__(message)
        self.city = city
        self.insurance_type = insurance_type
        self.visit_type = visit_type


class PolicyDataError(MediClearError):
    """政策数据文件缺失、无法解析或结构不符合预期。"""


class VersionNotFoundError(PolicyDataError):
    """请求的政策版本（年份）不存在。"""


class PolicyValidationError(PolicyDataError):
    """政策数据校验未通过。

    Attributes:
        errors: 校验错误列表，每项形如 ``{'path': 'beijing.employee.inpatient', 'message': '...'}``
    """

    def __init__(self, errors, source='<unknown>'):
        self.errors = list(errors)
        self.source = source
        preview = '; '.join(
            '{path}: {message}'.format(**e) for e in self.errors[:5]
        )
        more = '' if len(self.errors) <= 5 else ' （共 %d 条）' % len(self.errors)
        super().__init__('政策数据校验失败 [%s] %s%s' % (source, preview, more))


class InvalidInputError(MediClearError, ValueError):
    """调用方传入的参数不合法（费用为负、类型未知等）。"""


class InsufficientDataError(MediClearError):
    """计算所需的数据不足（例如未提供缴费基数却要算个人账户）。"""
