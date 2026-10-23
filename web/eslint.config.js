import js from '@eslint/js';
import globals from 'globals';

export default [
  js.configs.recommended,
  {
    files: ['js/**/*.js'],
    languageOptions: { ecmaVersion: 2023, sourceType: 'module', globals: globals.browser },
    rules: {
      'no-restricted-properties': ['error',
        { property: 'innerHTML', message: 'Build DOM with h(); never parse strings as HTML.' },
        { property: 'outerHTML', message: 'Build DOM with h(); never parse strings as HTML.' },
        { property: 'insertAdjacentHTML', message: 'Build DOM with h(); never parse strings as HTML.' }],
      'no-eval': 'error',
      'no-implied-eval': 'error',
    },
  },
  { files: ['test/**/*.js'], languageOptions: { ecmaVersion: 2023, sourceType: 'module', globals: globals.node } },
];
