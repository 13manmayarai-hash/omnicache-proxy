"""
AST-Guided Code Invalidation and Structural Parity Engine for OmniCache.

Mitigates the Code-Logic Semantic Dilemma where cosine vector distance evaluates
thematic proximity rather than logical equivalence. Detects critical structural changes
such as Boolean inversions (and vs or / && vs ||), boundary conditions (< vs <=),
call order permutations, and inverted return values before granting semantic cache hits.
"""

import ast
import re
from typing import List, Tuple, Optional, Any, Set


class ASTStructureVisitor(ast.NodeVisitor):
    """
    Extracts canonical structural signatures from Python Abstract Syntax Trees.
    Normalizes variable names and formatting while strictly preserving operators,
    control flow structure, and call sequences.
    """

    def __init__(self):
        self.signature: List[str] = []

    def _extract_decorator_name(self, dec_node: ast.AST) -> str:
        if isinstance(dec_node, ast.Name):
            return dec_node.id
        elif isinstance(dec_node, ast.Attribute):
            return f"{self._extract_decorator_name(dec_node.value)}.{dec_node.attr}"
        elif isinstance(dec_node, ast.Call):
            return self._extract_decorator_name(dec_node.func)
        return type(dec_node).__name__

    def generic_visit(self, node: ast.AST):
        node_name = type(node).__name__
        
        # 1. Boolean Operations (and / or)
        if isinstance(node, ast.boolop):
            self.signature.append(f"BoolOp:{node_name}")
        # 2. Comparison Operations (<, <=, >, >=, ==, !=, in, not in, is, is not)
        elif isinstance(node, ast.cmpop):
            self.signature.append(f"CmpOp:{node_name}")
        # 3. Binary Operators (+, -, *, /, %, &, |, ^, <<, >>)
        elif isinstance(node, ast.operator):
            self.signature.append(f"BinOp:{node_name}")
        # 4. Unary Operators (not, +, -)
        elif isinstance(node, ast.unaryop):
            self.signature.append(f"UnaryOp:{node_name}")
        # 5. Control Flow Statements
        elif isinstance(node, (ast.If, ast.While, ast.For, ast.Try, ast.With)):
            self.signature.append(f"Control:{node_name}")
        # 6. Return values (recording constant booleans/nulls)
        elif isinstance(node, ast.Return):
            if isinstance(node.value, ast.Constant):
                self.signature.append(f"Return:Constant({node.value.value})")
            else:
                self.signature.append("Return")
        # 7. Function Call Identifiers (to preserve call order)
        elif isinstance(node, ast.Call):
            call_id = ""
            if isinstance(node.func, ast.Name):
                call_id = node.func.id
            elif isinstance(node.func, ast.Attribute):
                call_id = node.func.attr
            self.signature.append(f"Call:{call_id}" if call_id else "Call")
        # 8. Function and Class Definitions (with decorator tracking)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in getattr(node, "decorator_list", []):
                dec_name = self._extract_decorator_name(dec)
                self.signature.append(f"Decorator:{dec_name}")
            self.signature.append(f"Def:{node.name}")
        elif isinstance(node, ast.ClassDef):
            for dec in getattr(node, "decorator_list", []):
                dec_name = self._extract_decorator_name(dec)
                self.signature.append(f"Decorator:{dec_name}")
            self.signature.append(f"Class:{node.name}")
        # 9. Asynchronous Await expressions
        elif isinstance(node, ast.Await):
            self.signature.append("Await")

        super().generic_visit(node)


class ASTValidator:
    """
    Validates structural and AST equivalence between query and cached code snippets.
    Provides multi-language structural tokenization fallback for non-Python languages
    (JavaScript, TypeScript, C, C++, Go, Rust, Java, SQL).
    """

    CODE_PATTERNS = [
        r"```",
        r"\bdef\s+\w+\s*\(",
        r"\bfunction\s+\w*\s*\(",
        r"\bclass\s+\w+[\s:(]",
        r"\bSELECT\s+.+\s+FROM\b",
        r"\bimport\s+[\w\.]+",
        r"\bfrom\s+[\w\.]+\s+import\b",
        r"\b(?:const|let|var)\s+\w+\s*=",
        r"\b(?:public|private|protected)\s+(?:class|void|int|function)\b",
        r"\bfunc\s+(?:\([^)]+\)\s*)?\w+\s*\(",
        r"\bfn\s+\w+\s*\(",
        r"(=>|->)\s*\{?",
        r"\bif\s*\(.+?\)\s*\{",
        r"\breturn\s+[A-Za-z0-9_\(\)\'\"]+"
    ]

    COMPILED_CODE_PATTERNS = [re.compile(p, re.IGNORECASE) for p in CODE_PATTERNS]

    @classmethod
    def contains_code_patterns(cls, text: str) -> bool:
        """Determines if a prompt contains code blocks or significant syntax constructs."""
        if not text or not isinstance(text, str):
            return False
        for pat in cls.COMPILED_CODE_PATTERNS:
            if pat.search(text):
                return True
        # Check if text is parseable Python statements (e.g. function calls, assignments)
        try:
            tree = ast.parse(text)
            if tree.body and not all(isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) for stmt in tree.body):
                return True
        except (SyntaxError, ValueError, MemoryError, RecursionError):
            pass
        return False

    @classmethod
    def extract_code_blocks(cls, text: str) -> List[str]:
        """
        Extracts code blocks from markdown fences (```lang ... ```).
        If no fenced blocks exist but code patterns are detected, returns the full text.
        """
        if not text:
            return []

        # 1. Fenced markdown code blocks
        fenced = re.findall(r"```(?:[a-zA-Z0-9_\-+]*\n)?(.*?)```", text, re.DOTALL)
        if fenced:
            return [b.strip() for b in fenced if b.strip()]

        # 2. Inline code spans (` ... `) if containing multi-token expressions
        inlines = re.findall(r"`([^`\n]{15,})`", text)
        if inlines:
            candidates = [i.strip() for i in inlines if cls.contains_code_patterns(i)]
            if candidates:
                return candidates

        # 3. Raw prompt if it contains code patterns
        if cls.contains_code_patterns(text):
            return [text.strip()]

        return []

    @classmethod
    def get_python_ast_signature(cls, code_str: str) -> Tuple[bool, List[str]]:
        """Parses Python code and extracts structural AST signature."""
        try:
            tree = ast.parse(code_str)
            visitor = ASTStructureVisitor()
            visitor.visit(tree)
            return True, visitor.signature
        except (SyntaxError, ValueError, MemoryError, RecursionError):
            return False, []

    @classmethod
    def get_operator_stream_signature(cls, code_str: str) -> List[str]:
        """
        Universal multi-language structural tokenizer.
        Strips comments and string literals, then captures the ordered sequence
        of operators, comparison boundaries, boolean logic, and control flow keywords.
        """
        # Strip line comments (// and #)
        code = re.sub(r"//.*", "", code_str)
        # Strip trailing and inline # comments
        code = re.sub(r"(?m)#.*$", "", code)
        # Strip block comments (/* ... */)
        code = re.sub(r"/\*.*?\*/", "", code, flags=re.DOTALL)

        # Structural operators and control tokens
        token_pattern = re.compile(
            r"(===|!==|==|!=|<=|>=|&&|\|\||<<|>>|\+\+|--|=>|->|[<>+\-*/%!=&|^~]|\b(?:if|else|while|for|return|break|continue|and|or|not|true|false|null|nil|none)\b)",
            re.IGNORECASE
        )
        raw_tokens = token_pattern.findall(code)
        tokens = [t.lower() if t.isalpha() else t for t in raw_tokens]
        return tokens

    @classmethod
    def get_signature(cls, code_str: str) -> Tuple[str, List[str]]:
        """
        Returns (engine_used, signature_tokens).
        Prefers Python AST when code parses cleanly, falls back to structural operator stream.
        """
        # Try optional tree-sitter if installed
        try:
            import tree_sitter  # type: ignore
            # If tree-sitter grammars are present, can be used for deep parsing
        except ImportError:
            pass

        # Try Python AST
        ok, py_sig = cls.get_python_ast_signature(code_str)
        if ok and py_sig:
            return "python_ast", py_sig

        # Fallback to universal operator stream
        op_sig = cls.get_operator_stream_signature(code_str)
        return "operator_stream", op_sig

    @classmethod
    def validate_structural_parity(cls, prompt_a: str, prompt_b: str) -> Tuple[bool, str]:
        """
        Compares structural syntax and operator flow between two prompts.
        Returns (is_valid, reason).
        """
        blocks_a = cls.extract_code_blocks(prompt_a)
        blocks_b = cls.extract_code_blocks(prompt_b)

        # Neither contains code: safe for standard cosine semantic matching
        if not blocks_a and not blocks_b:
            return True, "NO_CODE_PRESENT"

        # One prompt contains code, the other does not: structural divergence
        if bool(blocks_a) != bool(blocks_b):
            return False, "CODE_PRESENCE_ASYMMETRY: One prompt contains code blocks while the other does not"

        # Number of code blocks differs
        if len(blocks_a) != len(blocks_b):
            return False, f"CODE_BLOCK_COUNT_MISMATCH: Prompt has {len(blocks_a)} code blocks, cached entry has {len(blocks_b)}"

        # Validate each corresponding code block
        for idx, (code_a, code_b) in enumerate(zip(blocks_a, blocks_b)):
            engine_a, sig_a = cls.get_signature(code_a)
            engine_b, sig_b = cls.get_signature(code_b)

            if sig_a == sig_b:
                continue

            # Detect specific semantic divergence classes
            set_a = set(sig_a)
            set_b = set(sig_b)
            diff = set_a ^ set_b

            # 1. Boolean Inversion (and vs or / && vs ||)
            boolean_indicators = {"BoolOp:And", "BoolOp:Or", "and", "or", "&&", "||"}
            if diff & boolean_indicators:
                return False, f"AST_LOGICAL_DIVERGENCE: Boolean inversion detected in block {idx + 1} ({sig_a} vs {sig_b})"

            # 2. Boundary Condition (< vs <=, > vs >=, == vs !=)
            boundary_indicators = {
                "CmpOp:Lt", "CmpOp:LtE", "CmpOp:Gt", "CmpOp:GtE", "CmpOp:Eq", "CmpOp:NotEq",
                "<", "<=", ">", ">=", "==", "!=", "===", "!=="
            }
            if diff & boundary_indicators:
                return False, f"AST_BOUNDARY_DIVERGENCE: Comparison boundary condition mismatch in block {idx + 1} ({sig_a} vs {sig_b})"

            # 3. Return value divergence
            return_indicators = {t for t in diff if t.startswith("Return:Constant") or t in ("true", "false", "True", "False", "None", "null")}
            if return_indicators:
                return False, f"AST_RETURN_DIVERGENCE: Return value mismatch in block {idx + 1} ({sig_a} vs {sig_b})"

            # 4. Operator shift (+ vs -, * vs /)
            arith_indicators = {"BinOp:Add", "BinOp:Sub", "BinOp:Mult", "BinOp:Div", "+", "-", "*", "/"}
            if diff & arith_indicators:
                return False, f"AST_ARITHMETIC_DIVERGENCE: Operator mismatch in block {idx + 1} ({sig_a} vs {sig_b})"

            # 5. Decorator divergence
            dec_indicators = {t for t in diff if t.startswith("Decorator:")}
            if dec_indicators:
                return False, f"AST_DECORATOR_DIVERGENCE: Function/class decorator mismatch in block {idx + 1} ({sig_a} vs {sig_b})"

            # 6. Concurrency / Await divergence
            if "Await" in diff:
                return False, f"AST_CONCURRENCY_DIVERGENCE: Async/await execution mismatch in block {idx + 1} ({sig_a} vs {sig_b})"

            # 7. General structural / call ordering mismatch
            return False, f"AST_STRUCTURAL_DIVERGENCE: Code syntax structure mismatch in block {idx + 1} ({sig_a} vs {sig_b})"

        return True, "AST_STRUCTURAL_MATCH: Code syntax structures verified identical"
