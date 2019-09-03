from typing import Union, Optional

from sidekick import curry
from .operators import UnaryOp as UnaryOpEnum, BinaryOp as BinaryOpEnum
from ... import ast
from ...ast import Tree
from ...ast.utils import wrap_tokens, attr_property

PyAtom = (type(None), type(...), bool, int, float, complex, str, bytes)
PyAtomT = Union[None, bool, int, float, complex, str, bytes]  # ..., NotImplemented
PyContainerT = Union[list, tuple, set, dict]

__all__ = [
    "Expr",
    "ExprLeaf",
    "ExprNode",
    "to_expr",
    "register_expr",
    "Atom",
    "Name",
    "And",
    "Or",
    "BinOp",
    "UnaryOp",
    "GetAttr",
    "Call",
    "Ternary",
    "Call",
    "Lambda",
    "ArgDef",
    "Yield",
    "YieldFrom",
]


class Expr(ast.Expr):
    """
    Base class for Python AST nodes that represent expressions.
    """

    class Meta:
        root = True
        abstract = True

    def attr(self, attr: str) -> "Expr":
        return GetAttr(self, attr)

    def index(self, value) -> "Expr":
        return ...  # TODO

    def binary_op(self, op, other: "Expr") -> "Expr":
        op = BinaryOpEnum.from_name(op)
        return BinOp(op, self, other)

    def unary_op(self, op) -> "Expr":
        op = UnaryOpEnum.from_name(op)
        return UnaryOp(op, self)

    def comparison_op(self, op, other, *args) -> "ExprNode":
        return ...  # TODO


class ExprLeaf(ast.ExprLeaf, Expr):
    """
    Base class for Python Expression leaf nodes.
    """

    class Meta:
        abstract = True


class ExprNode(ast.ExprNode, Expr):
    """
    Base class for Python Expression leaf nodes.
    """

    class Meta:
        abstract = True


class Void(ast.VoidMixin, Expr):
    """
    Empty node.

    Used to represent absence in places that require an optional expression
    node.
    """

    def attr(self, attr: str) -> Expr:
        raise TypeError("invalid operation with void node: getattr")

    def index(self, value) -> Expr:
        raise TypeError("invalid operation with void node: getindex")

    def binary_op(self, op, other) -> Expr:
        raise TypeError("invalid operation with void node: binary operator")

    def unary_op(self, op) -> Expr:
        raise TypeError("invalid operation with void node: unary operator")

    def comparison_op(self, op, other, *args) -> Expr:
        raise TypeError("invalid operation with void node: comparison operator")

    def from_static_children(self):
        return Void()


to_expr = Expr._meta.coerce
register_expr = curry(2, to_expr.register)


# ==============================================================================
# LEAF NODES
# ==============================================================================


class Atom(ast.AtomMixin, ExprLeaf):
    """
    Atomic data such as numbers, strings, etc.

    Value is always known statically and is represented by the corresponding
    Python value.
    """

    class Meta:
        types = PyAtom

    def from_static_children(self, value):
        if isinstance(value, PyAtom):
            return Atom(value, **self._attrs)
        raise TypeError

    def _repr_as_child(self):
        return self._repr()

    def tokens(self, ctx):
        # Ellipsis is repr'd as "Ellipsis"
        yield "..." if self.value is ... else repr(self.value)


class Name(ast.NameMixin, ExprLeaf):
    """
    A Python name
    """

    class Meta:
        types = (str,)

    def from_static_children(self, value):
        return to_expr(value)


# ==============================================================================
# REGULAR NODES
# ==============================================================================


class And(ast.BinaryMixin, ExprNode):
    """
    Short-circuit "and" operator.
    """

    lhs: Expr
    rhs: Expr

    class Meta:
        sexpr_symbol = "and"
        command = "{lhs} and {rhs}"

    def from_static_children(self, lhs, rhs):
        return to_expr(lhs and rhs)


class Or(ast.BinaryMixin, ExprNode):
    """
    Short-circuit "or" operator.
    """

    lhs: Expr
    rhs: Expr

    class Meta:
        sexpr_symbol = "or"
        command = "{lhs} or {rhs}"

    def from_static_children(self, lhs, rhs):
        return to_expr(lhs or rhs)


class UnaryOp(ast.UnaryOpMixin, ExprNode):
    """
    Unary operators like +, -, ~ and not.
    """

    tag: UnaryOpEnum
    expr: Expr

    class Meta:
        sexpr_skip = ("+", "-")

    def tokens(self, ctx):
        yield self.op.value
        yield from self.expr.tokens(ctx)

    def from_static_children(self, child):
        return to_expr(self.tag.function(child))


class BinOp(ast.BinaryOpMixin, ExprNode):
    """
    Regular binary operators like for arithmetic and bitwise arithmetic
    operations. It excludes comparisons and bitwise operations since they are
    treated differently by Python grammar.
    """

    tag: BinaryOpEnum
    lhs: Expr
    rhs: Expr

    class Meta:
        sexpr_unary_op_class = UnaryOp

    def from_static_children(self, lhs, rhs):
        return to_expr(self.tag.function(lhs, rhs))

    def wrap_child_tokens(self, child, role):
        if isinstance(child, BinOp):
            return self.precedence_level > child.precedence_level
        elif isinstance(child, (UnaryOp, And, Or)):
            return True
        return False

    def tokens(self, ctx):
        yield from self.child_tokens(self.lhs, "lhs", ctx)
        yield f" {self.op.value} "
        yield from self.child_tokens(self.rhs, "rhs", ctx)


class Starred(ExprNode):
    """
    A starred or kw-starred element.

    Can only be present as function arguments or list, set, tuples and dictionary
    elements.
    """

    expr: Expr
    kwstar: bool = False

    def tokens(self, ctx):
        wrap = isinstance(self.expr, (And, Or))
        yield ("**" if self.kwstar else "*")
        yield from wrap_tokens(self.expr.tokens(ctx), wrap)


class Keyword(ExprNode):
    """
    A keyword argument.

    Can only be present as a child of a function call node.
    """

    expr: Expr
    name: str = attr_property("name")

    def __init__(self, expr, name, **kwargs):
        super().__init__(expr, name, **kwargs)

    def tokens(self, ctx):
        yield self.name
        yield "="
        yield from self.expr.tokens(ctx)


class GetAttr(ast.GetAttrMixin, ExprNode):
    """
    Get attribute expression.
    """

    expr: Expr
    attr: str

    def wrap_child_tokens(self, child, role):
        value = self.expr
        if isinstance(value, (BinOp, UnaryOp, And, Or)):
            return True
        if isinstance(value, Atom) and isinstance(value.value, (int, float, complex)):
            return True
        return False


class Call(ExprNode):
    """
    Call expression with argument list.
    """

    expr: Expr
    args: Tree

    # noinspection PyMethodParameters
    @classmethod
    def from_args(*args, **kwargs):
        """
        Create element that represents a function call from positional and
        keyword arguments passed to function.

        This is the same as creating a node with empty arguments, then adding
        values using the add_args() method.
        """
        cls, e, *args = args
        return cls(e, Tree("args", list(generate_args(*args, **kwargs))))

    _meta_fcall = from_args

    def add_args(*args, **kwargs):
        """
        Add argument(s) to argument tree. Return a list with new arguments.

        This function accepts several different signatures:

        call.add_args(expr):
            Add a simple argument. Argument must be a valid Python
            expression.
        call.add_args('*', name_or_str):
        call.add_args('*name'):
            Like before, but adds a star argument.
        call.add_args('**', name_or_str):
        call.add_args('**name'):
            Like before, but adds a double start (keyword expansion) argument.
        call.add_args(name=value):
            Add one or more keyword arguments.

        Those signatures can be combined.
        """
        self, *args = args
        args = list(generate_args(*args, **kwargs))
        self.args.children.extend(args)
        return args

    def tokens(self, ctx):
        e = self.expr
        if isinstance(e, (BinOp, UnaryOp, And, Or)):
            yield from wrap_tokens(self.expr.tokens(ctx))
        elif isinstance(e, Atom) and isinstance(e.value, (int, float, complex)):
            yield from wrap_tokens(self.expr.tokens(ctx))
        else:
            yield from self.expr.tokens(ctx)

        children = iter(self.args.children)

        yield "("
        try:
            arg = next(children)
        except StopIteration:
            pass
        else:
            yield from arg.tokens(ctx)
            for arg in children:
                yield ", "
                yield from arg.tokens(ctx)
        yield ")"


def generate_args(*args, **kwargs):
    """
    Yield arguments from a function call signature.
    """
    has_keyword = False
    msg = "cannot set positional argument after keyword argument"

    args = iter(args)
    for arg in args:
        if isinstance(arg, str):
            if arg.startswith("**"):
                has_keyword = True
                yield Starred(next(args) if arg == "**" else Name(arg[2:]), kwstar=True)
            elif arg.startswith("*"):
                if has_keyword:
                    raise ValueError(msg)
                yield Starred(next(args) if arg == "*" else Name(arg[1:]))
            else:
                raise TypeError("expect expression, got str")
        else:
            if not isinstance(arg, Expr):
                cls_name = arg.__class__.__name__
                raise TypeError(f"expect expression, got {cls_name}")
            yield arg

    for name, value in kwargs.items():
        yield Keyword(value, name=name)


class Yield(ast.CommandMixin, ExprNode):
    """
    "yield <expr>" expression.
    """

    expr: Expr

    class Meta:
        command = "(yield {expr})"


class YieldFrom(ast.CommandMixin, ExprNode):
    """
    "yield from <expr>" expression.
    """

    expr: Expr

    class Meta:
        command = "(yield from {expr})"


class Lambda(ExprNode):
    """
    Lambda function.
    """

    args: Tree
    expr: Expr
    vararg: Optional[str]
    kwarg: bool


class ArgDef(ExprNode):
    """
    Argument definition.
    """

    name: Expr
    default: Expr
    annotation: Expr

    def __init__(self, name, default=None, annotation=None, **kwargs):
        default = Void() if default is None else default
        annotation = Void() if annotation is None else annotation
        super().__init__(name, default, annotation, **kwargs)

    def tokens(self, ctx):
        yield from self.name.tokens(ctx)
        if self.annotation:
            yield ": "
            yield from self.annotation.tokens(ctx)
            if self.default:
                yield " = "
                yield from self.default.tokens(ctx)
        if self.default:
            yield "="
            yield from self.default.tokens(ctx)

    def __eq__(self, other):
        if (
            not isinstance(other, ArgDef)
            and isinstance(self.default, Void)
            and isinstance(self.annotation, Void)
        ):
            return self.name == other
        return super().__eq__(other)


class Ternary(ExprNode):
    """
    Ternary operator (<then> if <cond> else <other>)
    """

    cond: Expr
    then: Expr
    other: Expr

    def tokens(self, ctx):
        cond, then, other = self.cond, self.then, self.other
        yield from wrap_tokens(then.tokens(ctx), wrap=isinstance(then, Ternary))
        yield " if "
        yield from wrap_tokens(cond.tokens(ctx), wrap=isinstance(cond, Ternary))
        yield " else "
        yield from other.tokens(ctx)
