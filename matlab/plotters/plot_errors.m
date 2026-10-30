function plot_errors(ac_data)
%PLOT_ERRORS Error counters per bus: I2C_ERRORS and UART_ERRORS. Counters that stay 0 are left out.
    kinds = {'I2C_ERRORS', 'I2C'; 'UART_ERRORS', 'UART'};
    kinds = kinds(cellfun(@(m) pprz.has(ac_data, m), kinds(:, 1)), :);
    if isempty(kinds)
        return
    end
    axs = pprz.tiles(size(kinds, 1), 'Bus errors');
    for k = 1:size(kinds, 1)
        m = ac_data.(kinds{k, 1});
        fields = setdiff(fieldnames(m), {'t', 'bus_number', 'last_unexpected_event'}, 'stable');
        buses = unique(m.bus_number);
        for b = reshape(buses, 1, [])
            rows = m.bus_number == b;
            for i = 1:numel(fields)
                v = double(m.(fields{i})(rows));
                if any(v ~= 0)
                    plot(axs(k), m.t(rows), v, 'DisplayName', sprintf('bus %d %s', b, strrep(fields{i}, '_cnt', '')));
                end
            end
        end
        ylabel(axs(k), sprintf('%s errors', kinds{k, 2}));
        if isempty(findobj(axs(k), 'Type', 'line'))
            text(axs(k), 0.5, 0.5, 'No errors', 'Units', 'normalized', 'HorizontalAlignment', 'center');
        else
            legend(axs(k), 'Location', 'eastoutside', 'Interpreter', 'none');
        end
    end
end
