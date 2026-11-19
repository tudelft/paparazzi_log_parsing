function e = esc(d)
%ESC ESC telemetry in one form for old and new logs: one row per sample per motor.
%   e = pprz.esc(f.data)
%   Reads ESC_TELEMETRY (pprzlink 2026-11: one message with an array per field, indexed by idx) or else the
%   older ESC (one message per motor, motor_id). Both give the same fields; ones a message does not have are NaN:
%     t, id (actuator index), rpm [rpm], current [A], voltage [V] (ESC), supply_voltage [V] (ESC only),
%     temperature [°C] (ESC), motor_temperature [°C] (ESC_TELEMETRY), device_temperature [°C] (ESC only),
%     output [%], errors, power [W] and energy [Wh] (ESC only), and source (the message name).
%   Values older than 2.54 s (age 255 in ESC_TELEMETRY) are NaN. Without ESC telemetry e.t is empty.
%   See also pprz.servo, pprz.telemetry_rows.
    names = {'rpm', 'current', 'voltage', 'supply_voltage', 'temperature', 'motor_temperature', ...
        'device_temperature', 'output', 'errors', 'power', 'energy'};
    if pprz.has(d, 'ESC_TELEMETRY')
        m = d.ESC_TELEMETRY;
        map = {'rpm', 'rpm'; 'current', 'current'; 'voltage', 'voltage'; 'temperature', 'temperature'; ...
            'motor_temperature', 'motor_temperature'; 'output', 'output'; 'errors', 'errors'};
        e = pprz.telemetry_rows(m, map, names);
        e.source = 'ESC_TELEMETRY';
    elseif pprz.has(d, 'ESC')
        m = d.ESC;
        e.t = double(m.t(:));
        e.id = double(m.motor_id(:));
        map = {'rpm', 'rpm'; 'amps', 'current'; 'motor_volts', 'voltage'; 'bat_volts', 'supply_voltage'; ...
            'temperature', 'temperature'; 'temperature_dev', 'device_temperature'; 'power', 'power'; ...
            'energy', 'energy'};
        for k = 1:numel(names)
            e.(names{k}) = nan(size(e.t));
        end
        for k = 1:size(map, 1)
            if isfield(m, map{k, 1})
                e.(map{k, 2}) = double(m.(map{k, 1})(:));
            end
        end
        e.source = 'ESC';
    else
        e.t = zeros(0, 1);
        e.id = zeros(0, 1);
        for k = 1:numel(names)
            e.(names{k}) = zeros(0, 1);
        end
        e.source = '';
    end
end
