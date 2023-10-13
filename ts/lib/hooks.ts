// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

export type Callback = () => void | Promise<void>;

export function runHook(
    hooks: Array<Callback>,
): Promise<PromiseSettledResult<void | Promise<void>>[]> {
    const promises: (Promise<void> | void)[] = [];

    for (const hook of hooks) {
        try {
            const result = hook();
            promises.push(result);
        } catch (error) {
            console.log("Hook failed: ", error);
        }
    }

    return Promise.allSettled(promises);
}

export type Filter<T> = (arg: T) => T | Promise<T>;

export async function runFilter<R>(
    filters: Array<Filter<R>>,
    arg: R,
) {
    let result = arg;

    for (const filter of filters) {
        try {
            result = await Promise.resolve(filter(result));
        } catch (error) {
            console.log("Filter failed: ", error);
        }
    }

    return result;
}
