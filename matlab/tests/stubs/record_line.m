function h = record_line(fn, ax, x, y, varargin)
%RECORD_LINE Test stand-in helper: check one line (fn(ax, t, values, ...)) and remember its name.
    global PPRZ_TEST_LINES
    if nargin < 4 || ischar(y)
        error('%s: expected %s(ax, t, values, ...)', fn, fn);
    end
    if ~(isnumeric(y) || islogical(y))
        error('%s: values are %s', fn, class(y));
    end
    if numel(x) ~= size(y, 1) && numel(x) ~= numel(y)
        error('%s: %d times but %d values', fn, numel(x), numel(y));
    end
    name = '-';
    k = find(strcmp(varargin, 'DisplayName'), 1);
    if ~isempty(k)
        name = varargin{k + 1};
    end
    PPRZ_TEST_LINES{end + 1} = name;
    h = 1;
end
