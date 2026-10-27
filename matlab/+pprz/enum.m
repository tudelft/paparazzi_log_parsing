function names = enum(f, msg, field, values)
%ENUM Names of enum values, e.g. pprz.enum(f, 'ROTORCRAFT_STATUS', 'ap_mode', f.data.ROTORCRAFT_STATUS.ap_mode)
%   Values without a name give their number as text.
    names = string(values);
    if ~isfield(f.messages, msg) || ~isfield(f.messages.(msg), 'enums') || ~isfield(f.messages.(msg).enums, field)
        return
    end
    table = string(f.messages.(msg).enums.(field));
    ok = values >= 0 & values < numel(table) & values == round(values);
    names(ok) = table(values(ok) + 1);
end
