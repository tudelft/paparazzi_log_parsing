function plot_energy(ac_data)
%PLOT_ENERGY Battery voltage and current, power, and energy used (ENERGY).
    e = ac_data.ENERGY;
    axs = pprz.tiles(3, 'Energy');
    plot(axs(1), e.t, e.voltage, 'DisplayName', 'Voltage');
    ylabel(axs(1), 'Voltage [V]');
    plot(axs(2), e.t, e.current, 'DisplayName', 'Current');
    ylabel(axs(2), 'Current [A]');
    yyaxis(axs(2), 'right');
    plot(axs(2), e.t, e.power, 'DisplayName', 'Power');
    ylabel(axs(2), 'Power [W]');
    yyaxis(axs(2), 'left');
    if isfield(e, 'energy')
        plot(axs(3), e.t, e.energy, 'DisplayName', 'Energy');
        ylabel(axs(3), 'Energy used [Wh]');
    end
end
