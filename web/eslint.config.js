import js from '@eslint/js';
import ts from 'typescript-eslint';
import svelte from 'eslint-plugin-svelte';
import globals from 'globals';
import svelteConfig from './svelte.config.js';

export default ts.config(
  { ignores: ['dist/', 'node_modules/'] },
  js.configs.recommended,
  ...ts.configs.recommended,
  ...svelte.configs.recommended,
  { languageOptions: { globals: { ...globals.browser, ...globals.node, __MOCK_DEFAULT__: 'readonly' } } },
  {
    files: ['**/*.svelte', '**/*.svelte.ts'],
    languageOptions: { parserOptions: { projectService: true, extraFileExtensions: ['.svelte'], parser: ts.parser, svelteConfig } },
  },
  {
    rules: {
      '@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_', varsIgnorePattern: '^_' }],
      'svelte/no-at-html-tags': 'off',
      'svelte/require-each-key': 'off',
      'svelte/prefer-svelte-reactivity': 'off',
      'svelte/no-navigation-without-resolve': 'off',
    },
  },
);
