"""Exercise the real Starlark macro/action functions with inert Bazel objects."""

import ast
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from sonic_oci.owner_metadata import resolve


RULE = Path(__file__).resolve().parents[1] / "installed_packages.bzl"
PUBLIC_OWNER = "@@example+//:runtime_packages.json"
PRIVATE_OWNER = "@@example++source+private//:runtime_packages.json"
PUBLIC_SOURCE = "@@example+//:runtime"


class File:
    def __init__(self, path):
        self.path = path

    def __str__(self):
        return self.path


class Target:
    def __init__(self, label, files):
        self.label = label
        self.files = files

    def __getitem__(self, provider):
        return SimpleNamespace(files=SimpleNamespace(to_list=lambda: self.files))


class Arguments:
    def __init__(self):
        self.values = []

    def add(self, *values):
        self.values.extend(map(str, values))

    def add_all(self, values, before_each):
        for value in values:
            self.add(before_each, value)


class AliasRuleTest(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.actions = []

        def fail(message):
            raise ValueError(message)

        def declared(label):
            return str(label).replace("@public//", "@@example+//")

        namespace = {
            "native": SimpleNamespace(package_relative_label=declared),
            "_installed_packages": lambda **kwargs: self.calls.append(kwargs),
            "DefaultInfo": lambda **kwargs: SimpleNamespace(**kwargs),
            "OutputGroupInfo": lambda **kwargs: SimpleNamespace(**kwargs),
            "depset": list,
            "fail": fail,
        }
        parsed = ast.parse(RULE.read_text())
        functions = [node for node in parsed.body if isinstance(node, ast.FunctionDef)]
        exec(compile(ast.Module(body=functions, type_ignores=[]), str(RULE), "exec"), namespace)
        self.macro = namespace["installed_packages"]
        self.impl = namespace["_installed_packages_impl"]
        self.metadata = File("external/private/runtime_packages.json")
        self.payload = File("external/private/runtime.tar")
        self.module = File("selected/example.MODULE.bazel")

    def action(self, owners=None, metadata_files=None, resolved_owner=PRIVATE_OWNER):
        owners = ["@public//:runtime_packages.json"] if owners is None else owners
        self.macro(name="installed", owner_metadata=owners,
                   sources={"example": "@public//:runtime"})
        declared = self.calls[-1]
        files = [self.metadata] if metadata_files is None else metadata_files
        labels = declared["owner_metadata"]
        # Model either rule attr representation so this regression also rejects
        # the old alias-following implementation with its actual wrong argv.
        metadata_targets = ({label: Target(resolved_owner, files) for label in labels}
                            if isinstance(labels, dict) else [Target(resolved_owner, files) for _ in labels])
        attr = SimpleNamespace(
            owner_metadata=metadata_targets,
            sources={"example": Target("@@example++source+private//:runtime", [self.payload])},
            declared_sources=declared["declared_sources"],
            source_modules={"example": Target("//:MODULE.bazel", [self.module])},
            architecture="amd64",
        )
        args = Arguments()
        inputs = SimpleNamespace(base=File("base.tar"), apt_receipt=File("apt.json"),
                                 source_spec=File("policy.json"), normalization_receipt=None,
                                 retained_manifest=None, source_receipt=None)
        ctx = SimpleNamespace(
            label=SimpleNamespace(name="installed"), attr=attr, file=inputs,
            files=SimpleNamespace(controls=[], normalization_receipt=[]),
            executable=SimpleNamespace(_tool=File("installed_packages")),
            actions=SimpleNamespace(declare_file=File, args=lambda: args,
                                    run=lambda **kwargs: self.actions.append(kwargs)),
        )
        self.impl(ctx)
        return declared, args.values, self.actions[-1]

    def test_alias_argv_preserves_public_labels_and_resolved_input_files(self):
        declared, argv, action = self.action()
        self.assertIn(PUBLIC_OWNER + "=" + self.metadata.path, argv)
        self.assertEqual(declared["owner_metadata"], {PUBLIC_OWNER: "@public//:runtime_packages.json"})
        self.assertEqual(declared["declared_sources"], {"example": PUBLIC_SOURCE})
        self.assertIn("example=" + PUBLIC_SOURCE, argv)
        self.assertNotIn(PRIVATE_OWNER + "=" + self.metadata.path, argv)
        self.assertEqual([file.path for file in action["inputs"]],
                         ["base.tar", "apt.json", "policy.json", self.payload.path,
                          self.metadata.path, self.module.path])
        self.assertEqual([file.path for file in action["outputs"]], ["installed.status", "installed.json"])

    def test_nonalias_argument_and_inputs_remain_identical(self):
        _, argv, action = self.action(resolved_owner=PUBLIC_OWNER)
        self.assertEqual(argv, [
            "--base", "base.tar", "--apt-receipt", "apt.json", "--source-spec", "policy.json",
            "--architecture", "amd64", "--status", "installed.status", "--receipt", "installed.json",
            "--package-metadata", PUBLIC_OWNER + "=" + self.metadata.path,
            "--source-module", "example=" + self.module.path,
            "--source", "example=" + self.payload.path, "--source-label", "example=" + PUBLIC_SOURCE,
        ])
        self.assertIn(self.metadata, action["inputs"])

    def test_metadata_requires_exactly_one_output_before_action(self):
        for files in ([], [self.metadata, File("unexpected.json")]):
            with self.subTest(files=len(files)), self.assertRaisesRegex(ValueError, "one JSON file"):
                self.action(metadata_files=files)
        self.assertEqual(self.actions, [])

    def test_duplicate_declared_owner_is_rejected_before_action(self):
        for owners in (["@public//:runtime_packages.json"] * 2,
                       ["@public//:runtime_packages.json", PUBLIC_OWNER]):
            with self.subTest(owners=owners), self.assertRaisesRegex(ValueError, "duplicate metadata input"):
                self.action(owners=owners)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.actions, [])

    def test_distinct_public_aliases_are_retained_even_for_one_resolved_file(self):
        declared, argv, action = self.action(owners=["@public//:runtime_packages.json", "@public//:second.json"])
        self.assertEqual(list(declared["owner_metadata"]), [PUBLIC_OWNER, "@@example+//:second.json"])
        self.assertEqual(argv.count("--package-metadata"), 2)
        self.assertIn("@@example+//:second.json=" + self.metadata.path, argv)
        self.assertEqual(action["inputs"].count(self.metadata), 2)

    def test_absent_owner_metadata_retains_empty_default(self):
        self.macro(name="empty")
        self.assertEqual(self.calls[-1]["owner_metadata"], {})
        _, argv, _ = self.action(owners=[])
        self.assertNotIn("--package-metadata", argv)

    def test_strict_resolver_keeps_alias_and_duplicate_owner_checks(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            module = root / "MODULE.bazel"
            module.write_text('module(name="example", version="1.0.0.sonic.1")\n')
            owner = root / "owner.json"
            owner.write_text(json.dumps({"schema": 1, "module": "example", "packages": {"example": {
                "runtime_target": "//:runtime", "control_fields": {"Package": "example", "Version": "1"},
                "required_paths": ["usr/lib/libexample.so"],
            }}}))
            before = owner.read_bytes()
            policy = {"schema": 1, "packages": {}}
            modules, sources = {"example": module}, {"example": PUBLIC_SOURCE}
            with self.assertRaisesRegex(ValueError, "owner runtime target"):
                resolve(policy, {PRIVATE_OWNER: owner}, modules, sources, "amd64")
            result, _ = resolve(policy, {PUBLIC_OWNER: owner}, modules, sources, "amd64")
            self.assertEqual(result["packages"]["example"]["target"], PUBLIC_SOURCE)
            for wrong in ("@@foreign+//:runtime", "@@example+//:wrong"):
                with self.subTest(wrong=wrong), self.assertRaisesRegex(ValueError, "owner runtime target"):
                    resolve(policy, {PUBLIC_OWNER: owner}, modules, {"example": wrong}, "amd64")
            with self.assertRaisesRegex(ValueError, "multiple source owners"):
                resolve(policy, {PUBLIC_OWNER: owner, "@@example+//:second.json": owner}, modules, sources, "amd64")
            self.assertEqual(owner.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
