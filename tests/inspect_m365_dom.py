#!/usr/bin/env python3
"""Inspect the M365 Copilot chat page DOM from C3 browser."""
import asyncio
import sys
sys.path.insert(0, "/browser-auth")
from cookie_extractor import _get_context, _get_or_create_page

async def main():
    ctx = await _get_context()
    page = await _get_or_create_page(ctx)
    print(f"URL: {page.url}")
    title = await page.title()
    print(f"Title: {title}")

    info = await page.evaluate("""() => {
        const results = [];
        // All textareas
        document.querySelectorAll('textarea').forEach(el => {
            results.push({
                tag: 'textarea',
                placeholder: el.placeholder,
                id: el.id,
                cls: el.className.substring(0, 80),
                visible: el.offsetParent !== null
            });
        });
        // All contenteditable
        document.querySelectorAll('[contenteditable]').forEach(el => {
            results.push({
                tag: el.tagName,
                ce: el.contentEditable,
                role: el.getAttribute('role'),
                ariaLabel: el.getAttribute('aria-label'),
                placeholder: el.getAttribute('placeholder') || el.getAttribute('data-placeholder'),
                cls: el.className.substring(0, 80),
                visible: el.offsetParent !== null,
                text: el.textContent.substring(0, 50)
            });
        });
        // All inputs type=text
        document.querySelectorAll('input[type=text]').forEach(el => {
            results.push({
                tag: 'input',
                placeholder: el.placeholder,
                id: el.id,
                visible: el.offsetParent !== null
            });
        });
        // Buttons with send/submit
        document.querySelectorAll('button').forEach(el => {
            const label = el.getAttribute('aria-label') || '';
            const text = el.textContent.trim().substring(0, 40);
            const testid = el.getAttribute('data-testid') || '';
            if (label.toLowerCase().includes('send') || label.toLowerCase().includes('submit')
                || text.toLowerCase().includes('send') || testid.includes('send') || testid.includes('submit')) {
                results.push({
                    tag: 'button',
                    ariaLabel: label,
                    text: text,
                    testid: testid,
                    disabled: el.disabled,
                    visible: el.offsetParent !== null
                });
            }
        });
        // Check for iframes
        document.querySelectorAll('iframe').forEach(el => {
            results.push({
                tag: 'iframe',
                src: (el.src || '').substring(0, 120),
                id: el.id,
                visible: el.offsetParent !== null
            });
        });
        return results;
    }""")

    print(f"\nFound {len(info)} elements:")
    for item in info:
        print(item)

asyncio.run(main())
