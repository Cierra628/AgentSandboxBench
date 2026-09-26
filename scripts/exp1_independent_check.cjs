// Independent property checks; these are not the benchmark's hidden test patch.
const prettier = require('/testbed');
(async () => {
  const cases = {
    svg: '<svg><script>const answer=1+2;</script></svg>',
    nested_svg: '<div><svg><script>const answer=1+2;</script></svg></div>',
    html_control: '<script>const answer=1+2;</script>',
  };
  const results = {};
  for (const [name, input] of Object.entries(cases)) {
    const output = await prettier.format(input, {parser: 'html'});
    results[name] = {
      javascript_formatted: output.includes('const answer = 1 + 2;'),
      idempotent: await prettier.format(output, {parser: 'html'}) === output,
      output,
    };
  }
  console.log(JSON.stringify(results));
  process.exitCode = Object.values(results).every(x => x.javascript_formatted && x.idempotent) ? 0 : 1;
})().catch(error => { console.error(error); process.exitCode = 2; });
