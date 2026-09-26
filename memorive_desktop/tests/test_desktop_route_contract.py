"""Check the integrated router against the actual DOM and page controllers."""
import importlib.util
import ast
import json
import re
import unittest
import tempfile
import shutil
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path

DESKTOP = Path(__file__).resolve().parents[1]


class Elements(HTMLParser):
    def __init__(self):
        super().__init__()
        self.elements = []

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


class DesktopRouteContract(unittest.TestCase):
    def test_native_diagnostic_uses_actual_routes_and_probe_fields(self):
        app = ast.parse((DESKTOP / 'product/desktop/app.py').read_text('utf8'))
        expected = next(ast.literal_eval(node.value) for node in app.body
                        if isinstance(node, ast.Assign) and any(
                            isinstance(target, ast.Name) and target.id == 'EXPECTED_DESKTOP_ROUTES'
                            for target in node.targets))
        text = (DESKTOP / 'product/desktop/bundle_integrated.html').read_text('utf8')
        routes = json.loads(re.search(r'const routes = Object.freeze\((\{.*?\})\);', text, re.S)[1])
        self.assertEqual(set(expected), set(routes))
        diagnostic = next(node for node in app.body if isinstance(node, ast.FunctionDef) and node.name == 'run_test')
        probe = next(node.value.args[0].value for node in ast.walk(diagnostic)
                     if isinstance(node, ast.Assign) and any(
                         isinstance(target, ast.Name) and target.id == 'inspect' for target in node.targets)
                     and isinstance(node.value, ast.Call))
        fields = set(re.findall(r'\b([A-Za-z][A-Za-z0-9_]*)\s*:', probe))
        accesses = {node.slice.value for node in ast.walk(diagnostic)
                    if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name)
                    and node.value.id == 'inspect' and isinstance(node.slice, ast.Constant)}
        self.assertTrue(accesses)
        self.assertFalse(accesses - fields, accesses - fields)

    def test_svg_resources_have_valid_encoded_xml(self):
        for path in (DESKTOP / 'product/desktop/assets').rglob('*.svg'):
            with self.subTest(asset=path.name):
                ET.fromstring(path.read_bytes())

    def test_public_roots_parse_and_tampering_blocks_packaging(self):
        spec = importlib.util.spec_from_file_location('build_memorive', DESKTOP / 'tools/build_memorive.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertGreater(module.check_public_roots(DESKTOP / 'source')['certificates'], 0)
        with tempfile.TemporaryDirectory(prefix='memorive-cert-check-') as temporary:
            root = Path(temporary)
            target = root / 'memorive_settings'
            target.mkdir()
            for name in ('metadata_transport.py', 'public_roots.pem'):
                shutil.copyfile(DESKTOP / 'source/memorive_settings' / name, target / name)
            with (target / 'public_roots.pem').open('ab') as stream:
                stream.write(b'changed')
            with self.assertRaisesRegex(ValueError, 'PUBLIC_ROOTS_INTEGRITY_MISMATCH'):
                module.check_public_roots(root)

    def test_routes_resolve_to_sized_roots_and_existing_controllers(self):
        text = (DESKTOP / 'product/desktop/bundle_integrated.html').read_text('utf8')
        document = Elements()
        document.feed(text)
        routes = json.loads(re.search(r'const routes = Object.freeze\((\{.*?\})\);', text, re.S)[1])
        surfaces = {attrs['data-desktop-route'] for _, attrs in document.elements if 'data-desktop-route' in attrs}
        self.assertEqual(set(routes), surfaces)
        roots = {attrs['id'] for _, attrs in document.elements if 'desktop-page-root' in attrs.get('class', '').split()}
        self.assertEqual(len(roots), len(routes))
        for route, descriptor in routes.items():
            with self.subTest(route=route):
                component = descriptor['component']
                self.assertIsInstance(component, str)
                self.assertIn(f'desktop-{component}-preview', roots)
                self.assertRegex(text, rf'window\.DesktopPreview_{re.escape(component)}\s*=')
        self.assertIn('`#desktop-${descriptor.component}-preview`', text)
        self.assertIn('`DesktopPreview_${descriptor.component}`', text)
        self.assertRegex(text, r'\.desktop-route-surface > \.desktop-page-root,[\s\S]{0,150}height: 100% !important;')

    def test_effective_script_policy(self):
        spec = importlib.util.spec_from_file_location('build_memorive', DESKTOP / 'tools/build_memorive.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for name in ('bundle_integrated.html', 'bundle_assistant.html'):
            with self.subTest(page=name):
                self.assertEqual(module.check_inline_script_policy(DESKTOP / 'product/desktop' / name)['status'], 'PASS')


if __name__ == '__main__':
    unittest.main()
