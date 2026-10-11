// Independent known outputs and idempotence, separate from frozen tools.
const prettier = require('/testbed');
const cases = [
  ['union', 'type A = ((number | string))["x"];', 'type A = (number | string)["x"];\n'],
  ['intersection', 'type A = ((B & C))["x"];', 'type A = (B & C)["x"];\n'],
  ['keyof', 'type A = ((keyof T))[K];', 'type A = (keyof T)[K];\n'],
  ['conditional', 'type A = ((B extends C ? D : E))[K];', 'type A = (B extends C ? D : E)[K];\n'],
  ['array', 'type A = ((number | string))[];', 'type A = (number | string)[];\n']
];
const results = cases.map(([name, source, expected]) => {
  const output = prettier.format(source, {parser:'typescript'});
  return {name, output, expected, matches:output===expected,
          idempotent:prettier.format(output,{parser:'typescript'})===output};
});
const baseline = process.argv.includes('--baseline');
const passed = baseline ? !results[0].matches : results.every(r=>r.matches&&r.idempotent);
console.log(JSON.stringify({status:passed?'PASS':'FAIL', phase:baseline?'unfixed_negative_control':'fixed_properties', results}));
if (!passed) process.exitCode = 1;
