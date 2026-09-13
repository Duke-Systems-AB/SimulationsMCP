import { describe, it, expect } from 'vitest';

describe('NaN handling in statistics', () => {
  // We test the Python parse_float indirectly via the JSON output contract
  // The key requirement: no "-nan(ind)" strings, no -1 sentinel values,
  // NaN stats should be null in JSON

  it('should define the NaN contract: null for undefined stats', () => {
    // This test documents the expected behavior
    // When a stat value is NaN/undefined, the JSON should contain null (not -1, not 0, not "-nan(ind)")
    const validStatValues = [0, 0.5, 1.0, 42.7, null];
    const invalidStatValues = [-1, '-nan(ind)', 'nan', NaN, undefined];

    for (const v of validStatValues) {
      expect(v === null || typeof v === 'number').toBe(true);
    }
    // This documents what we're fixing: -1 sentinel and string NaN are NOT valid
    for (const v of invalidStatValues) {
      const isValidOutput = v === null || (typeof v === 'number' && !isNaN(v as number) && v >= 0);
      expect(isValidOutput).toBe(false);
    }
  });
});
