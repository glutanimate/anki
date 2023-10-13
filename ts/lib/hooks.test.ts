// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

import { runFilter, runHook } from "./hooks";

test("runHook without arg", async () => {
    const hooks = [
        jest.fn(),
        jest.fn(),
        jest.fn(),
        jest.fn(() => Promise.resolve()),
    ];

    await runHook(hooks);

    for (const hook of hooks) {
        expect(hook).toHaveBeenCalled();
    }
});

test("runHook with arg", async () => {
    const hooks = [
        jest.fn(),
        jest.fn(),
        jest.fn(),
        jest.fn(() => Promise.resolve()),
    ];

    await runHook(hooks, 0);

    for (const hook of hooks) {
        expect(hook).toHaveBeenCalledWith(0);
    }
});

test("runFilter", async () => {
    const callOrder: number[] = [];

    const filters = [
        jest.fn((arg) => {
            callOrder.push(0);
            return arg + 1;
        }),
        jest.fn((arg) => {
            const promise = new Promise((resolve) => {
                setTimeout(() => {
                    callOrder.push(1);
                    resolve(arg + 1);
                }, 100);
            });
            return promise;
        }),
        jest.fn((arg) => {
            callOrder.push(2);
            return arg + 1;
        }),
    ];

    const result = await runFilter(filters, 0);

    expect(result).toBe(3);
    expect(callOrder).toEqual([0, 1, 2]);

    for (const filter of filters) {
        expect(filter).toHaveBeenCalled();
    }
});
