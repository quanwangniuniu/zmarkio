import {
    estimateTokenCount,
    fitExternalUrlPromptToContext,
    truncateRelevantTextByCharacters,
} from '@/lib/pageTextBudget';

const PAGE_TEXT_START = 'Page text:\n---\n';
const PAGE_TEXT_END = '\n---\n\nInstruction:';

function extractPageText(prompt: string): string {
    const start = prompt.indexOf(PAGE_TEXT_START) + PAGE_TEXT_START.length;
    const end = prompt.indexOf(PAGE_TEXT_END, start);
    return prompt.slice(start, end);
}

describe('External URL page-text budgeting', () => {
    it('fits a long page and system prompt in the configured context while keeping a relevant late section', () => {
        const systemPrompt = ('Detect the source language and preserve the advertised offer. ').repeat(60);
        const longPage = ('Generic page content that is not ad relevant. ').repeat(445)
            + '\nProduct benefit: UNIQUE_LATE_SELLING_POINT, free shipping and 20% off.';
        expect(longPage.length).toBeGreaterThan(20_000);

        const userPrompt = (
            'Identify the actual advertisement on this public page and create a variation.\n\n'
            + PAGE_TEXT_START
            + longPage
            + PAGE_TEXT_END
            + ' create a fresh variation and preserve the source language.'
        );
        const numCtx = 8192;
        const reservedOutputTokens = 1024;
        const fitted = fitExternalUrlPromptToContext(
            systemPrompt,
            userPrompt,
            numCtx,
            reservedOutputTokens,
        );
        const estimatedTotal = estimateTokenCount(systemPrompt)
            + estimateTokenCount(fitted)
            + reservedOutputTokens
            + Math.max(64, Math.ceil(numCtx * 0.02));

        expect(estimatedTotal).toBeLessThanOrEqual(numCtx);
        expect(fitted.length).toBeLessThan(userPrompt.length);
        expect(extractPageText(fitted)).toContain('UNIQUE_LATE_SELLING_POINT');
    });

    it('keeps high-value lines when the extracted page exceeds the character cap', () => {
        const page = ('Cookie preferences and generic footer navigation.\n').repeat(800)
            + '\nProduct name: Trail Jacket\nMain benefit: waterproof protection in wet weather\nPrice: $99 with free shipping';
        const result = truncateRelevantTextByCharacters(page, 2000);

        expect(result.length).toBeLessThanOrEqual(2000);
        expect(result).toContain('Product name: Trail Jacket');
        expect(result).toContain('waterproof protection');
        expect(result).toContain('$99');
    });

    it('does not alter prompts that are not External URL prompts', () => {
        const prompt = 'Source ad copy: a warm jacket. Write one new variation.';
        expect(fitExternalUrlPromptToContext('system prompt', prompt, 8192)).toBe(prompt);
    });
});
