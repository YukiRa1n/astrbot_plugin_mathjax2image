"""
安全数学表达式求值器
使用 AST 白名单机制替代危险的 eval()
"""

import ast
import math
from typing import Any

from astrbot.api import logger


class SafeMathEvaluator(ast.NodeVisitor):
    """
    安全的数学表达式求值器

    使用 AST 白名单机制，只允许安全的数学操作：
    - 二元运算: +, -, *, /, //, %, **
    - 一元运算: +x, -x
    - 函数: sqrt, sin, cos, tan, exp, log, log10, abs, ceil, floor
    - 常量: pi, e
    """

    # 白名单：允许的函数
    ALLOWED_FUNCTIONS = {
        "sqrt": math.sqrt,
        "sin": math.sin,
        "cos": math.cos,
        "tan": math.tan,
        "exp": math.exp,
        "log": math.log,
        "log10": math.log10,
        "abs": abs,
        "ceil": math.ceil,
        "floor": math.floor,
    }

    # 白名单：允许的常量
    ALLOWED_CONSTANTS = {
        "pi": math.pi,
        "e": math.e,
    }

    # 白名单：允许的二元运算符
    ALLOWED_BINARY_OPS = {
        ast.Add: lambda x, y: x + y,
        ast.Sub: lambda x, y: x - y,
        ast.Mult: lambda x, y: x * y,
        ast.Div: lambda x, y: x / y,
        ast.FloorDiv: lambda x, y: x // y,
        ast.Mod: lambda x, y: x % y,
        ast.Pow: lambda x, y: x**y,
    }

    # 白名单：允许的一元运算符
    ALLOWED_UNARY_OPS = {
        ast.UAdd: lambda x: +x,
        ast.USub: lambda x: -x,
    }

    def visit_Expression(self, node: ast.Expression) -> float:
        """访问表达式根节点"""
        logger.debug("[SafeEval] [ENTRY] visit_Expression")
        return self.visit(node.body)

    def visit_Constant(self, node: ast.Constant) -> float:
        """访问常量节点（数字）"""
        if isinstance(node.value, (int, float)):
            logger.debug(f"[SafeEval] [PROCESS] Constant value={node.value}")
            return float(node.value)
        raise ValueError(f"Unsupported constant type: {type(node.value)}")

    def visit_Num(self, node: ast.Num) -> float:
        """访问数字节点（Python 3.7 兼容）"""
        logger.debug(f"[SafeEval] [PROCESS] Num value={node.n}")
        return float(node.n)

    def visit_Name(self, node: ast.Name) -> float:
        """访问变量名节点（常量）"""
        name = node.id
        if name in self.ALLOWED_CONSTANTS:
            value = self.ALLOWED_CONSTANTS[name]
            logger.debug(f"[SafeEval] [PROCESS] Name constant={name} value={value}")
            return value
        raise ValueError(f"Unsupported variable: {name}")

    def visit_BinOp(self, node: ast.BinOp) -> float:
        """访问二元运算节点"""
        op_type = type(node.op)
        if op_type not in self.ALLOWED_BINARY_OPS:
            raise ValueError(f"Unsupported binary operator: {op_type.__name__}")

        left = self.visit(node.left)
        right = self.visit(node.right)
        op_func = self.ALLOWED_BINARY_OPS[op_type]
        result = op_func(left, right)
        logger.debug(
            f"[SafeEval] [PROCESS] BinOp op={op_type.__name__} left={left} right={right} result={result}"
        )
        return result

    def visit_UnaryOp(self, node: ast.UnaryOp) -> float:
        """访问一元运算节点"""
        op_type = type(node.op)
        if op_type not in self.ALLOWED_UNARY_OPS:
            raise ValueError(f"Unsupported unary operator: {op_type.__name__}")

        operand = self.visit(node.operand)
        op_func = self.ALLOWED_UNARY_OPS[op_type]
        result = op_func(operand)
        logger.debug(
            f"[SafeEval] [PROCESS] UnaryOp op={op_type.__name__} operand={operand} result={result}"
        )
        return result

    def visit_Call(self, node: ast.Call) -> float:
        """访问函数调用节点"""
        if not isinstance(node.func, ast.Name):
            raise ValueError("Only simple function calls are allowed")

        func_name = node.func.id
        if func_name not in self.ALLOWED_FUNCTIONS:
            raise ValueError(f"Unsupported function: {func_name}")

        # 求值所有参数
        args = [self.visit(arg) for arg in node.args]
        if node.keywords:
            raise ValueError("Keyword arguments are not allowed")

        # 调用白名单函数
        func = self.ALLOWED_FUNCTIONS[func_name]
        result = func(*args)
        logger.debug(
            f"[SafeEval] [PROCESS] Call func={func_name} args={args} result={result}"
        )
        return result

    def generic_visit(self, node: ast.AST) -> Any:
        """拒绝所有未明确允许的节点类型"""
        node_type = type(node).__name__
        logger.warning(f"[SafeEval] [ERROR] Blocked unsafe node type={node_type}")
        raise ValueError(f"Unsupported AST node type: {node_type}")


def safe_eval_math(expr: str) -> float:
    """
    安全地求值数学表达式

    Args:
        expr: 数学表达式字符串，如 "2 + 3 * sqrt(16)"

    Returns:
        float: 计算结果，失败时返回 float('nan')

    Examples:
        >>> safe_eval_math("2 + 3")
        5.0
        >>> safe_eval_math("sqrt(16)")
        4.0
        >>> safe_eval_math("sin(pi / 2)")
        1.0
        >>> safe_eval_math("__import__('os').system('ls')")
        nan
    """
    logger.debug(f"[SafeEval] [ENTRY] safe_eval_math expr={expr[:50]}")

    try:
        # 解析表达式为 AST
        tree = ast.parse(expr, mode="eval")
        logger.debug("[SafeEval] [PROCESS] AST parsed successfully")

        # 使用白名单求值器
        evaluator = SafeMathEvaluator()
        result = evaluator.visit(tree)

        logger.info(f"[SafeEval] [EXIT] safe_eval_math result={result}")
        return float(result)

    except Exception as e:
        logger.warning(
            f"[SafeEval] [ERROR] Evaluation failed expr={expr[:50]} error={type(e).__name__}: {e}"
        )
        return float("nan")
