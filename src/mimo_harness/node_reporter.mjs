// Declaration-ordered node:test evidence. Entry files run serially; no terminal parsing.
import fs from 'node:fs';
import path from 'node:path';

export default async function* reporter(source) {
  const root = process.env.MIMO_NODE_ROOT;
  const output = process.env.MIMO_NODE_EVIDENCE;
  const data = {schema_version: 1, adapter: 'node', cases: {}, collected: [],
    collection_errors: [], collection_skips: [], complete: false, runner_version: process.version};
  const parents = new Map();
  const identities = new Map();
  let summary;
  function identity(d) {
    if (!d.file || !Number.isInteger(d.testId)) throw new Error('NODE_EVENT_ID_MISSING');
    const file = path.relative(root, d.file).split(path.sep).join('/');
    if (!file || file.startsWith('../') || path.isAbsolute(file)) throw new Error('NODE_TEST_OUTSIDE_WORKSPACE');
    const parent = d.parentId && parents.get(d.parentId);
    if (d.nesting > 0 && !parent) throw new Error('NODE_PARENT_ID_MISSING');
    const names = [...(d.nesting > 0 ? parent.names : []), d.name];
    return {nodeid: `${file}::${names.join(' > ')}`, file, names};
  }
  function wrapper(d) {
    return d.file && d.name === d.file;
  }
  try {
    for await (const event of source) {
      const d = event.data;
      if (event.type === 'test:summary' && d.file === undefined) { summary = d; continue; }
      if (event.type === 'test:stdout' || event.type === 'test:stderr') {
        yield d.message; continue;
      }
      if (event.type === 'test:start' && !wrapper(d)) {
        const item = identity(d);
        parents.set(d.testId, item);
        identities.set(`${d.file}:${d.testId}`, item);
      }
      if (!['test:pass', 'test:fail'].includes(event.type)) continue;
      const error = d.details?.error;
      if (wrapper(d)) {
        if (event.type === 'test:fail') data.collection_errors.push({nodeid: d.name, message: String(error?.stack || error)});
        continue; // A successful empty file is not an executed behavior test.
      }
      const item = identities.get(`${d.file}:${d.testId}`);
      if (!item) throw new Error('NODE_RESULT_WITHOUT_START');
      const skipped = d.skip !== undefined && d.skip !== false;
      const todo = d.todo !== undefined && d.todo !== false;
      if (d.details?.type === 'suite') {
        if (skipped || todo) data.collection_skips.push({nodeid: item.nodeid, message: 'Skipped/TODO suite'});
        else if (event.type === 'test:fail' && error?.failureType !== 'subtestsFailed')
          data.collection_errors.push({nodeid: item.nodeid, message: String(error?.stack || error)});
        continue;
      }
      const assertion = error?.code === 'ERR_ASSERTION' || error?.cause?.code === 'ERR_ASSERTION';
      let status = event.type === 'test:pass' ? 'passed' : assertion || error?.failureType === 'subtestsFailed' ? 'failed' : 'error';
      if (skipped) status = 'skipped';
      else if (todo) status = event.type === 'test:pass' ? 'xpass' : 'xfail';
      if (Object.hasOwn(data.cases, item.nodeid)) throw new Error('DUPLICATE_TEST_IDS');
      data.cases[item.nodeid] = {nodeid: item.nodeid, file: item.file, status,
        assertion_failure: status === 'failed' && assertion, phases: {call: status},
        message: error ? String(error.cause?.stack || error.stack || error).slice(0, 8192) : ''};
      data.collected.push(item.nodeid);
    }
    if (!summary) throw new Error('NODE_INCOMPLETE_STREAM');
    data.summary = summary;
    data.complete = true;
    const counts = summary.counts;
    if (!counts || counts.cancelled) throw new Error('NODE_CANCELLED_TESTS');
    // Wrapper-only results are deliberately excluded. Other missing results fail closed.
    if (data.collected.length && counts.tests !== data.collected.length) throw new Error('NODE_CASE_COUNT_MISMATCH');
  } catch (error) {
    data.runner_error = String(error.message || error);
  }
  fs.writeFileSync(output, JSON.stringify(data), 'utf8');
}
