import tempfile
import unittest
from pathlib import Path

import contract_lint
import task_patch
import task_state
import workspace


class ContractLintTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())

    def write_contracts(self, **overrides):
        base = {
            "schema_version": 1,
            "project": {"name": "demo", "language": "Python", "runtime": "3.12"},
            "vocabulary": [
                {"term": "invoice_id", "owner": "billing", "kind": "identifier",
                 "type": "str", "format": "^INV-[0-9]+$",
                 "forbidden_aliases": ["inv_id"]},
            ],
            "shared_kernel": [],
            "commands": [
                {"name": "invoice create", "verb": "create", "resource": "invoice",
                 "owner": "billing", "handler": "billing.cli.register",
                 "args": ["customer", "items"]},
            ],
            "modules": [
                {"id": "billing", "path": "src/billing", "depends_on": ["pricing"],
                 "exports": [], "consumes": []},
                {"id": "pricing", "path": "src/pricing", "depends_on": [],
                 "exports": [{"symbol": "calculate_price", "kind": "function"}],
                 "consumes": []},
                {"id": "auth", "path": "src/auth", "depends_on": [],
                 "exports": [], "consumes": []},
            ],
            "recipes": [],
        }
        base.update(overrides)
        workspace.write_contracts(self.root, base)

    def check(self, path, content):
        return contract_lint.check_patch(self.root, [{"path": path, "after": content}])

    def test_legal_patch_generates_contract_delta(self) -> None:
        self.write_contracts()
        result = self.check(
            "src/billing/invoice.py",
            "from pricing import calculate_price\n\ndef create_invoice():\n    return calculate_price()\n",
        )
        self.assertEqual([], result["violations"])
        self.assertEqual(["create_invoice"], [item["symbol"] for item in result["contract_delta"]])

    def test_dependency_boundary_violation(self) -> None:
        self.write_contracts()
        result = self.check("src/auth/login.py", "import billing\n")
        self.assertTrue(any("depends_on" in item for item in result["violations"]))
        self.assertTrue(any("billing" in item for item in result["violations"]))

    def test_symbol_boundary_violation(self) -> None:
        self.write_contracts()
        result = self.check("src/billing/invoice.py", "from pricing import calculate_tax\n")
        self.assertTrue(any("calculate_tax" in item and "exports" in item for item in result["violations"]))

    def test_private_symbol_violation(self) -> None:
        self.write_contracts()
        result = self.check("src/billing/invoice.py", "from pricing import _secret\n")
        self.assertTrue(any("私有符号" in item for item in result["violations"]))

    def test_duplicate_definition_violation(self) -> None:
        self.write_contracts()
        result = self.check(
            "src/billing/invoice.py",
            "def calculate_price():\n    return 1\n",
        )
        self.assertTrue(any("calculate_price" in item and "export" in item for item in result["violations"]))

    def test_command_conflict_violation(self) -> None:
        self.write_contracts()
        result = self.check("src/auth/login.py", "cli.register_command('invoice create')\n")
        self.assertTrue(any("invoice create" in item and "不能重复注册" in item for item in result["violations"]))

    def test_command_name_in_comment_is_not_a_conflict(self) -> None:
        self.write_contracts()
        result = self.check("src/auth/login.py", "# invoice create\n")
        self.assertFalse(result["violations"])

    def test_shared_kernel_admission_violation(self) -> None:
        self.write_contracts()
        result = self.check("src/shared/errors.py", "class AppError:\n    pass\n")
        self.assertTrue(any("shared_kernel" in item for item in result["violations"]))

    def test_vocabulary_alias_violation(self) -> None:
        self.write_contracts()
        result = self.check("src/auth/login.py", "def inv_id():\n    return 1\n")
        self.assertTrue(any("inv_id" in item and "禁止别名" in item for item in result["violations"]))


class ContractLintApplyPatchTest(unittest.TestCase):
    def test_apply_task_patch_blocks_violations(self) -> None:
        root = Path(tempfile.mkdtemp())
        workspace.write_contracts(root, {
            "schema_version": 1,
            "project": {"name": "demo", "language": "Python", "runtime": "3.12"},
            "vocabulary": [],
            "shared_kernel": [],
            "commands": [],
            "modules": [
                {"id": "auth", "path": "src/auth", "depends_on": [],
                 "exports": [], "consumes": []},
                {"id": "billing", "path": "src/billing", "depends_on": [],
                 "exports": [], "consumes": []},
            ],
            "recipes": [],
        })
        state = {
            "schema_version": 1,
            "task_version": 1,
            "tasks": [{
                "id": "auth-task",
                "module_id": "auth",
                "status": "review",
                "summary": "login",
                "target_files": ["src/auth/login.py"],
                "verification": [],
                "patch": [{"path": "src/auth/login.py", "after": "import billing\n", "before": ""}],
            }],
            "last_error": "",
        }
        task_state.write_task_state(root, state)
        result = task_patch.apply_task_patch(root, "auth-task")
        task = result["tasks"][0]
        self.assertEqual("failed", task["status"])
        self.assertIn("契约校验未通过", task["last_error"])
        self.assertFalse((root / "src" / "auth" / "login.py").exists())

    def test_apply_task_patch_merges_contract_delta(self) -> None:
        root = Path(tempfile.mkdtemp())
        workspace.write_contracts(root, {
            "schema_version": 1,
            "project": {"name": "demo", "language": "Python", "runtime": "3.12"},
            "vocabulary": [],
            "shared_kernel": [],
            "commands": [],
            "modules": [
                {"id": "auth", "path": "src/auth", "depends_on": [],
                 "exports": [], "consumes": []},
            ],
            "recipes": [],
        })
        state = {
            "schema_version": 1,
            "task_version": 1,
            "tasks": [{
                "id": "auth-task",
                "module_id": "auth",
                "status": "review",
                "summary": "login",
                "target_files": ["src/auth/login.py"],
                "verification": [],
                "patch": [{
                    "path": "src/auth/login.py",
                    "after": "def login():\n    return True\n",
                    "before": "",
                }],
            }],
            "last_error": "",
        }
        task_state.write_task_state(root, state)
        result = task_patch.apply_task_patch(root, "auth-task")
        task = result["tasks"][0]
        self.assertEqual("applied", task["status"])
        self.assertEqual([], task["contract_lint_violations"])
        self.assertTrue((root / "src" / "auth" / "login.py").exists())
        contracts = workspace.read_contracts(root)
        exports = next(m for m in contracts["modules"] if m["id"] == "auth")["exports"]
        self.assertEqual(["login"], [item["symbol"] for item in exports])


class ContractLintVerifyTaskTest(unittest.TestCase):
    def test_verify_task_blocks_contract_violation(self) -> None:
        import task_verify
        root = Path(tempfile.mkdtemp())
        workspace.write_contracts(root, {
            "schema_version": 1,
            "project": {"name": "demo", "language": "Python", "runtime": "3.12"},
            "vocabulary": [],
            "shared_kernel": [],
            "commands": [],
            "modules": [
                {"id": "auth", "path": "src/auth", "depends_on": [],
                 "exports": [], "consumes": []},
                {"id": "billing", "path": "src/billing", "depends_on": [],
                 "exports": [], "consumes": []},
            ],
            "recipes": [],
        })
        (root / "src" / "auth").mkdir(parents=True)
        (root / "src" / "auth" / "login.py").write_text("import billing\n", encoding="utf-8")
        state = {
            "schema_version": 1,
            "task_version": 1,
            "tasks": [{
                "id": "auth-task",
                "module_id": "auth",
                "status": "applied",
                "summary": "login",
                "target_files": ["src/auth/login.py"],
                "verification": [],
                "patch": [],
            }],
            "last_error": "",
        }
        task_state.write_task_state(root, state)
        result = task_verify.verify_task(root, "auth-task")
        task = result["tasks"][0]
        self.assertEqual("failed", task["status"])
        self.assertIn("契约校验未通过", task["last_error"])


if __name__ == "__main__":
    unittest.main()
