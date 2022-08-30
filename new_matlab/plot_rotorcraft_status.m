function plot_rotorcraft_status(ac_data)
    % Plot the Rotorcraft Status
    if isfield(ac_data, 'ROTORCRAFT_STATUS')
        subplot(2,1,1);
        area(ac_data.ROTORCRAFT_STATUS.timestamp, [ac_data.ROTORCRAFT_STATUS.ap_in_flight, ac_data.ROTORCRAFT_STATUS.ap_motors_on]);
        title('Rotorcraft Status');
        xlabel('Time [s]');
        legend('In flight', 'Motors on');

        subplot(2,1,2);
        plot(ac_data.ROTORCRAFT_STATUS.timestamp, [ac_data.ROTORCRAFT_STATUS.ap_mode, ac_data.ROTORCRAFT_STATUS.arming_status]);
        title('Rotorcraft Status');
        xlabel('Time [s]');
        legend('Flight mode', 'Arming status');
        sgtitle('Status')
    end
end
