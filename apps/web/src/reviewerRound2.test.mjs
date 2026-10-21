import test from 'node:test';
import assert from 'node:assert/strict';
import { recordConcentrationLine } from './views/evidence/recordConcentration.ts';
import { sqlTokenDiff } from './views/evidence/sqlDiff.ts';
import { bridgeGeometry } from './views/evidence/bridgeGeometry.ts';

test('bridge accumulates cents exactly beyond floating-point precision',()=>{
  const plot=bridgeGeometry('90071992547409930.00',['0.01','-0.02'],'90071992547409929.99');
  assert.deepEqual(plot.levels,[9007199254740993000n,9007199254740993001n,9007199254740992999n,9007199254740992999n]);
  assert.ok(plot.ratio(plot.levels[1])>plot.ratio(plot.levels[0]));
});

test('record concentration distinguishes dispersed and concentrated changes exactly', () => {
  const dispersed = {changed:1000, top_changes:Array.from({length:10}, () => ({delta:'1.20'}))};
  assert.equal(recordConcentrationLine(dispersed, {delta_target_minus_baseline:'1000.00'}), 'Top 10 records explain 1.2% of the difference — spread across many records.');
  const concentrated = {changed:20, top_changes:Array.from({length:5}, () => ({delta:'17.00'}))};
  assert.equal(recordConcentrationLine(concentrated, {delta_target_minus_baseline:'100.00'}), 'Top 5 records explain 85% of the difference — a few large records.');
});

test('concentration never rounds large money through floating point or claims cancellation exceeds 100 percent', () => {
  const summary = {changed:10, abs_change_total:'900719925474099300.00', top_changes:[{delta:'90071992547409930.00'}]};
  assert.match(recordConcentrationLine(summary, {delta_target_minus_baseline:'900719925474099300.00'}), /10%/);
  assert.match(recordConcentrationLine({changed:2,abs_change_total:'200',top_changes:[{delta:'110'},{delta:'-90'}]}, {delta_target_minus_baseline:'20'}), /absolute record changes.*offset/);
  assert.equal(recordConcentrationLine({changed:0,top_changes:[]}, {delta_target_minus_baseline:'0'}), '');
});

test('SQL token diff highlights added WHERE and preserves both originals', () => {
  const before = 'SELECT SUM(amount) FROM snapshot';
  const after = "SELECT SUM(amount) FROM snapshot WHERE status = 'paid'";
  const diff = sqlTokenDiff(before, after);
  assert.equal(diff.filter(p => p.kind !== 'added').map(p => p.text).join(''), before);
  assert.equal(diff.filter(p => p.kind !== 'removed').map(p => p.text).join(''), after);
  assert.match(diff.filter(p => p.kind === 'added').map(p => p.text).join(''), /WHERE status = 'paid'/);
});

test('SQL diff handles literals, multiline comments, replacements and bounded long queries', () => {
  for (const [before, after] of [["SELECT 'a b', 10 -- old\nFROM snapshot", "SELECT 'a b', 20 -- new\nFROM snapshot"],
      ['SELECT /* x */ SUM(amount) FROM snapshot', 'SELECT /* x */ COUNT(*) FROM snapshot'],
      ['SELECT '+ 'x + '.repeat(1000)+'x', 'SELECT '+ 'y + '.repeat(1000)+'y'], ['', 'SELECT 1']]) {
    const diff = sqlTokenDiff(before, after);
    assert.equal(diff.filter(p => p.kind !== 'added').map(p => p.text).join(''), before);
    assert.equal(diff.filter(p => p.kind !== 'removed').map(p => p.text).join(''), after);
  }
});
