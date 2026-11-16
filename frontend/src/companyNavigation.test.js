import test from 'node:test';
import assert from 'node:assert/strict';
import { companyHash, companyShareUrl, parseRoute, validSearchId } from './companyNavigation.js';

test('abre links diretos e mantém o ID entre abas da empresa', () => {
  const id = parseRoute('#/overview/42').searchId;
  assert.equal(id, '42');
  assert.equal(companyHash('processes', id), '#/processes/42');
  assert.equal(companyHash('predictions', id), '#/predictions/42');
  assert.equal(companyHash('history', id), '#/history');
});

test('link compartilhado aponta para a visão geral da consulta', () => {
  assert.equal(companyShareUrl({ href: 'https://example.com/app/#/predictions/42' }, 42),
    'https://example.com/app/#/overview/42');
  assert.throws(() => companyShareUrl({ href: 'https://example.com/' }, null));
});

test('rotas antigas funcionam e IDs inválidos não são aceitos', () => {
  assert.deepEqual(parseRoute('#/overview'), { page: 'overview', searchId: null });
  assert.deepEqual(parseRoute('#/history/2'), { page: 'history', searchId: null });
  assert.deepEqual(parseRoute('#/invalid'), { page: 'search', searchId: null });
  for (const id of ['0', '-2', 'foo', '12a', '1.5', '9007199254740992', null]) assert.equal(validSearchId(id), null);
});
