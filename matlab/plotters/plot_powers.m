function plot_powers(ac_data)
    if ~isfield(ac_data, 'POWER_DEVICE')
        return
    end


    ac_data.POWER_DEVICE.id = double(ac_data.POWER_DEVICE.node_id) + double(ac_data.POWER_DEVICE.circuit) ./ 100;
    pwr = unique(ac_data.POWER_DEVICE.id)

    figure
    hold off
    leg = []
    for i=1:size(pwr,1)
        p=pwr(i)
        leg =  [leg, num2str(p)]
        id = find(ac_data.POWER_DEVICE.id == p);
        plot(ac_data.POWER_DEVICE.current(id) .* ac_data.POWER_DEVICE.voltage(id))
        hold on
    end
    grid on
    legend(strcat('Node ',string(pwr)));
