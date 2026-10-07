#!/usr/bin/env python3
"""Check the fixed final package set using Debian metadata and version ordering."""

from dataclasses import FrozenInstanceError
import unittest

from sonic_apt import dependencies as subject


def package(name, version="1.0", **fields):
    return subject.Package(name, version, fields.pop("architecture", "amd64"), **fields)


class DependenciesTest(unittest.TestCase):
    def validate(self, *packages):
        return subject.validate({item.name: item for item in packages}, architecture="amd64")

    def test_control_fields_preserve_folded_dependencies_and_provider_metadata(self):
        """Keep continuation lines and Multi-Arch data needed for real binary relationships."""
        record = subject.package_from_control(
            b"Package: runner\nVersion: 1:2.0-3\nArchitecture: all\n"
            b"Depends: missing | python3:any (>= 3.0),\n python-runtime\n"
            b"Pre-Depends: libc6 (>= 2.30)\nProvides: runner-api (= 2.0)\nMulti-Arch: foreign\n",
            origin="runner/control")
        self.assertEqual(record.name, "runner")
        self.assertEqual(record.version, "1:2.0-3")
        self.assertEqual(record.multi_arch, "foreign")
        self.assertEqual(record.provides, "runner-api (= 2.0)")
        receipt = self.validate(record, package("python3", "3.13", multi_arch="allowed",
                                                provides="python-runtime"), package("libc6", "2.41"))
        self.assertEqual((receipt["depends_groups"], receipt["pre_depends_groups"]), (2, 1))
        with self.assertRaises(FrozenInstanceError):
            record.version = "2.0"

    def test_status_uses_only_installed_packages_and_preserves_their_requirements(self):
        """Do not count removed or merely unpacked packages as available base providers."""
        status = ("Package: base-app\nVersion: 1\nArchitecture: amd64\nStatus: install ok installed\n"
                  "Depends: helper (>= 2)\n\nPackage: helper\nVersion: 2\nArchitecture: amd64\n"
                  "Status: install ok unpacked\n\nPackage: old-config\nStatus: deinstall ok config-files\n")
        installed = subject.installed_packages(status, origin="base/status")
        self.assertEqual(set(installed), {"base-app"})
        with self.assertRaisesRegex(ValueError, "base-app.*base/status.*unsatisfied Depends.*helper"):
            subject.validate(installed, architecture="amd64")
        for desired in ("hold", "deinstall"):
            with self.subTest(desired=desired):
                current = status.replace("install ok unpacked", desired + " ok installed")
                installed = subject.installed_packages(current, origin="base/status")
                self.assertEqual(set(installed), {"base-app", "helper"})
                subject.validate(installed, architecture="amd64")

    def test_control_fields_roundtrip_preserves_metadata_for_child_layers(self):
        """Carry runtime additions into debug validation without treating diagnostic origin as metadata."""
        record = package("retained", "2:1.0-3", architecture="all", depends="helper | alternate",
                         pre_depends="setup (>= 2)", provides="virtual-api (= 1)",
                         multi_arch="foreign", origin="runtime archive")
        fields = subject.control_fields(record)
        self.assertEqual(fields, {
            "Package": "retained", "Version": "2:1.0-3", "Architecture": "all",
            "Depends": "helper | alternate", "Pre-Depends": "setup (>= 2)",
            "Provides": "virtual-api (= 1)", "Multi-Arch": "foreign",
        })
        restored = subject.package_from_fields(fields, origin="runtime receipt")
        self.assertEqual(subject.control_fields(restored), fields)
        self.assertEqual(restored.origin, "runtime receipt")

    def test_newer_fips_base_satisfies_minimum_but_older_base_is_rejected(self):
        """Validate the retained version, without requiring the lock's exact candidate version."""
        consumer = package("tcpdump", depends="libssl3t64 (>= 3.0.0)")
        self.validate(consumer, package("libssl3t64", "3.5.7-1~deb13u2+fips", origin="base"))
        with self.assertRaisesRegex(ValueError, "tcpdump.*unsatisfied Depends: libssl3t64"):
            self.validate(consumer, package("libssl3t64", "2.9.9", origin="base"))

    def test_debian_epoch_revision_and_tilde_ordering(self):
        """Use Debian's comparator where lexical and Python package ordering would disagree."""
        cases = (("2:1.0-1", ">>", "1:99.0-9"), ("1.0-10", ">>", "1.0-2"),
                 ("1.0~rc1", "<<", "1.0"), ("1.0", "=", "1.0-0"),
                 ("3.5.7-1~deb13u2+fips", ">=", "3.5.7-1~deb13u2"),
                 ("1.0-2", "<=", "1.0-2"))
        for actual, operator, expected in cases:
            with self.subTest(actual=actual, operator=operator, expected=expected):
                self.validate(package("consumer", depends=f"provider ({operator} {expected})"),
                              package("provider", actual))
        with self.assertRaisesRegex(ValueError, "unsatisfied Depends"):
            self.validate(package("consumer", depends="provider (>= 1.0)"), package("provider", "1.0~rc1"))

    def test_each_and_group_requires_one_satisfied_alternative(self):
        """Accept a later usable alternative, but never use it to bypass another required group."""
        consumer = package("consumer", depends="absent | older (>= 2) | newer (>= 3), support")
        self.validate(consumer, package("older", "1"), package("newer", "3"), package("support"))
        with self.assertRaisesRegex(ValueError, "unsatisfied Depends: support"):
            self.validate(consumer, package("older", "1"), package("newer", "3"))

    def test_versioned_provides_uses_declared_virtual_version(self):
        """A virtual version comes from Provides, not the concrete provider's package version."""
        provider = package("implementation", "99", provides="virtual-api (= 2), unversioned-api")
        self.validate(package("consumer", depends="virtual-api (>= 2), unversioned-api"), provider)
        for expression in ("virtual-api (>= 3)", "unversioned-api (>= 1)"):
            with self.subTest(expression=expression), self.assertRaisesRegex(ValueError, "unsatisfied Depends"):
                self.validate(package("consumer", depends=expression), provider)

    def test_virtual_provider_can_satisfy_dependency_when_concrete_name_is_too_old(self):
        """Search all installed providers instead of stopping at an incompatible concrete package."""
        self.validate(package("consumer", depends="virtual-api (>= 2)"), package("virtual-api", "1"),
                      package("implementation", "1", provides="virtual-api (= 2)"))

    def test_target_and_architecture_all_packages_and_any_qualifier(self):
        """Respect :any's Multi-Arch opt-in even when every payload targets the same architecture."""
        for arch in ("amd64", "all"):
            with self.subTest(architecture=arch):
                self.validate(package("consumer", depends="interpreter:any (>= 3), docs:amd64"),
                              package("interpreter", "3", architecture=arch, multi_arch="allowed"),
                              package("docs", architecture="all"))
        for value in ("no", "same", "foreign"):
            with self.subTest(multi_arch=value), self.assertRaisesRegex(ValueError, "unsatisfied Depends"):
                self.validate(package("consumer", depends="interpreter:any"),
                              package("interpreter", multi_arch=value))

    def test_any_qualifier_on_virtual_dependency_uses_provider_multi_arch(self):
        """Virtual names inherit the concrete provider's eligibility for :any dependencies."""
        consumer = package("consumer", depends="virtual-api:any (>= 2)")
        self.validate(consumer, package("implementation", provides="virtual-api (= 2)", multi_arch="allowed"))
        with self.assertRaisesRegex(ValueError, "unsatisfied Depends"):
            self.validate(consumer, package("implementation", provides="virtual-api (= 2)", multi_arch="foreign"))

    def test_all_final_packages_are_checked_including_retained_ones(self):
        """A retained provider's own missing dependency must not disappear from the validation scope."""
        retained = package("retained", depends="absent", origin="Make manifest")
        with self.assertRaisesRegex(ValueError, "retained.*Make manifest.*unsatisfied Depends: absent"):
            self.validate(package("consumer", depends="retained"), retained)

    def test_predepends_checks_presence_and_version_but_not_installation_order(self):
        """Check Pre-Depends constraints without claiming that archive assembly configures packages."""
        consumer = package("consumer", pre_depends="setup (>= 2)")
        for providers in ((), (package("setup", "1"),)):
            with self.subTest(providers=providers), self.assertRaisesRegex(ValueError, "unsatisfied Pre-Depends"):
                self.validate(consumer, *providers)
        receipt = self.validate(consumer, package("setup", "2"))
        self.assertEqual(receipt["pre_depends_groups"], 1)
        self.assertIn("installation ordering is not checked", receipt["scope"])

    def test_malformed_or_unsupported_alternatives_cannot_hide_behind_a_valid_one(self):
        """Reject parser fallbacks and unsupported source syntax before considering a usable alternative."""
        invalid = ("", "bad (=> 1)", "bad (> 1)", "bad (>=)", "bad (= nope)", "bad (>= 1-)",
                   "bad [amd64]", "bad [ ]", "bad <!nocheck>", "bad:native", "bad:arm64", "UPPER", "${misc:Depends}")
        for expression in invalid:
            with self.subTest(expression=expression), self.assertRaises(ValueError):
                self.validate(package("consumer", depends="present | " + expression), package("present"))
        for expression in (",present", "present,", "present,,present"):
            with self.subTest(expression=expression), self.assertRaises(ValueError):
                self.validate(package("consumer", depends=expression), package("present"))

    def test_invalid_provides_are_not_treated_as_available_names(self):
        """Require exact virtual versions and reject ambiguous or unsupported provider declarations."""
        for expression in ("virtual-api (>= 1)", "virtual-api (= 1-)", "virtual-api | alternate",
                           "virtual-api:any", "virtual-api:native"):
            with self.subTest(expression=expression), self.assertRaises(ValueError):
                self.validate(package("provider", provides=expression))

    def test_foreign_package_or_inconsistent_final_mapping_is_rejected(self):
        """Do not silently use an unsupported foreign payload or a record filed under another name."""
        with self.assertRaisesRegex(ValueError, "foreign package architecture"):
            self.validate(package("provider", architecture="arm64"))
        with self.assertRaisesRegex(ValueError, "invalid final package record"):
            subject.validate({"different-name": package("provider")}, architecture="amd64")

    def test_invalid_control_cannot_drop_requirements_during_tolerant_parsing(self):
        """Reject duplicate fields, damaged lines and multiple stanzas instead of losing dependency data."""
        valid = "Package: consumer\nVersion: 1\nArchitecture: amd64\n"
        for data in (valid + "Depends: first\ndepends: second\n", valid + "Depends missing-colon\n",
                     " Depends: orphan\n" + valid, valid + "\n" + valid, "", valid + "\x00"):
            with self.subTest(data=data), self.assertRaises(ValueError):
                subject.package_from_control(data, origin="control")
        with self.assertRaisesRegex(ValueError, "duplicate installed package"):
            subject.installed_packages((valid + "Status: install ok installed\n\n") * 2, origin="status")

    def test_retained_control_metadata_requires_identity_and_valid_debian_versions(self):
        """A source hash or malformed version cannot stand in for retained package dependency metadata."""
        with self.assertRaisesRegex(ValueError, "missing control fields"):
            subject.package_from_fields({"Package": "retained"}, origin="Make manifest")
        for version in ("", "not-a-version", "1 2", "1.0_bad", "1-", "1--", "1-2-", "1:1-", "١:1", "1.0\n"):
            with self.subTest(version=version), self.assertRaisesRegex(ValueError, "invalid Debian version"):
                package("retained", version)


if __name__ == "__main__":
    unittest.main()
