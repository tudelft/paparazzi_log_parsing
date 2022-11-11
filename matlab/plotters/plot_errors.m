function plot_errors(ac_data)
    % Plot the I2C errors
    ax1 = subplot(2,1,1);
    if isfield(ac_data, 'I2C_ERRORS')
        busses = unique(ac_data.I2C_ERRORS.bus_number);
        
        for i = 1:length(busses)
            idx = find(ac_data.I2C_ERRORS.bus_number == busses(i));
            plot(ac_data.I2C_ERRORS.timestamp(idx), [ac_data.I2C_ERRORS.wd_reset_cnt(idx), ac_data.I2C_ERRORS.queue_full_cnt(idx), ac_data.I2C_ERRORS.acknowledge_failure_cnt(idx), ac_data.I2C_ERRORS.misplaced_start_or_stop_cnt(idx), ac_data.I2C_ERRORS.arbitration_lost_cnt(idx), ac_data.I2C_ERRORS.overrun_or_underrun_cnt(idx), ac_data.I2C_ERRORS.pec_error_in_reception_cnt(idx), ac_data.I2C_ERRORS.timeout_or_tlow_error_cnt(idx), ac_data.I2C_ERRORS.smbus_alert_cnt(idx), ac_data.I2C_ERRORS.unexpected_event_cnt(idx), ac_data.I2C_ERRORS.last_unexpected_event(idx)]);
            legend('wd\_reset\_cnt', 'queue\_full\_cnt', 'acknowledge\_failure\_cnt', 'misplaced\_start\_or\_stop\_cnt', 'arbitration\_lost\_cnt', 'overrun\_or\_underrun\_cnt', 'pec\_error\_in\_reception\_cnt', 'timeout\_or\_tlow\_error\_cnt', 'smbus\_alert\_cnt', 'unexpected\_event\_cnt', 'last\_unexpected\_event');
            hold on
        end
        
        title('I2C errors');
        xlabel('Time [s]');
    end
end