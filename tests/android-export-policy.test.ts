import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile, mkdtemp, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';

test('Android export accepts selected journey lists and rejects duplicate or unknown download parameters', async () => {
  const source = await readFile(new URL('../android/app/src/main/java/com/mojing/game/WorldExportPolicy.java', import.meta.url), 'utf8');
  const production = source.replace(/^package .*;\s*/m, '').replace(/^import .*;\s*/gm, '');
  const driver = `import java.util.*;
class ExportPolicyTest {
  static void check(boolean value) { if (!value) throw new AssertionError("Export query policy"); }
  public static void main(String[] args) {
    String id = "12345678-abcd-4321-8900-1234567890ab";
    check(WorldExportPolicy.allows(Map.of("part",List.of("world"))));
    check(WorldExportPolicy.allows(Map.of("part",List.of("zip"),"journeys",List.of(id+","+id))));
    check(WorldExportPolicy.allows(Map.of("part",List.of("zip"),"destination",List.of("folder"),"journeys",List.of(id))));
    check(!WorldExportPolicy.allows(Map.of("part",List.of("zip","world"))));
    check(!WorldExportPolicy.allows(Map.of("part",List.of("zip"),"url",List.of("https://other.invalid"))));
    check(!WorldExportPolicy.allows(Map.of("part",List.of("world"),"journeys",List.of(id))));
    check(!WorldExportPolicy.allows(Map.of("part",List.of("zip"),"journeys",List.of(id+","))));
    check(!WorldExportPolicy.allows(Map.of("part",List.of("zip"),"journeys",List.of("../private"))));
    check(!WorldExportPolicy.allows(Map.of("part",List.of("zip"),"destination",List.of("external"))));
    check(!WorldExportPolicy.allows(Map.of("journeys",List.of(id))));
    System.out.println("EXPORT_POLICY_OK");
  }
}
${production}`;
  const directory = await mkdtemp(join(tmpdir(), 'mojing-export-policy-'));
  try {
    const file = join(directory, 'ExportPolicyTest.java');
    await writeFile(file, driver);
    const { stdout } = await promisify(execFile)('java', [file], { timeout: 30000 });
    assert.match(stdout, /EXPORT_POLICY_OK/);
  } finally { await rm(directory, { recursive: true, force: true }); }
});
