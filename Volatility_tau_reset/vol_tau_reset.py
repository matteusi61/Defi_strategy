from dataclasses import dataclass
from typing import List

from fractal.core.base import (Action, ActionToTake, BaseStrategy,
                               BaseStrategyParams, NamedEntity)
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))
from Modified_entity.uniswap_v3_lp_modified import UniswapV3LPConfig, UniswapV3LPEntity


@dataclass
class VolTauResetParams(BaseStrategyParams):
    INITIAL_BALANCE: float
    INFO_TIME : int
    ALPHA : float
    C : float


class VolTauResetStrategy(BaseStrategy):
    token0_decimals: int = -1
    token1_decimals: int = -1
    tick_spacing: int = -1
    tau: int = 30
    time: int = 0
    

    def __init__(self, params: VolTauResetParams, debug: bool = False, *args, **kwargs):
        self._params: VolTauResetParams = None  # set for type hinting
        assert self.token0_decimals != -1 and self.token1_decimals != -1 and self.tick_spacing != -1
        super().__init__(params=params, debug=debug, *args, **kwargs)
        self.deposited_initial_funds = False
        self.distribution = []

    def set_up(self):
        self.register_entity(NamedEntity(
            entity_name='UNISWAP_V3',
            entity=UniswapV3LPEntity(
                UniswapV3LPConfig(
                    token0_decimals=self.token0_decimals,
                    token1_decimals=self.token1_decimals
                )
            )
        ))
        assert isinstance(self.get_entity('UNISWAP_V3'), UniswapV3LPEntity)

    def _recalculate_tau(self):
        prices = np.array(self.distribution)
        prices = np.log(prices[1:]) - np.log(prices[:-1])
        Q1 = np.percentile(prices, 25)
        Q3 = np.percentile(prices, 75)
        IQR = Q3 - Q1
        std = np.std(prices, ddof = 1)
        self.tau = self._params.C * (self._params.ALPHA * std + (1 - self._params.ALPHA) * IQR)


    def predict(self) -> List[ActionToTake]:
        # Retrieve the pool state from the registered entity
        uniswap_entity: UniswapV3LPEntity = self.get_entity('UNISWAP_V3')
        global_state = uniswap_entity.global_state
        current_price = global_state.price  # Get the current market price
        self.distribution.append(current_price)
        self.time += 1
        if self.time > self._params.INFO_TIME:
            self._recalculate_tau()
            self.distribution = []
            self.time = 0

        # Check if we need to deposit funds into the LP before proceeding
        if not uniswap_entity.is_position and not self.deposited_initial_funds:
            self._debug("No active position. Depositing initial funds...")
            self.deposited_initial_funds = True
            return self._deposit_to_lp()

        if not uniswap_entity.is_position:
            self._debug("No active position. Run first rebalance")
            return self._rebalance()

        # Calculate the boundaries of the price range (bucket)
        lower_bound, upper_bound = uniswap_entity.internal_state.price_lower, uniswap_entity.internal_state.price_upper

        # If the price moves outside the range, reallocate liquidity
        if current_price < lower_bound or current_price > upper_bound:
            self._debug(f"Rebalance {current_price} moved outside range [{lower_bound}, {upper_bound}].")
            return self._rebalance()
        return []

    def _deposit_to_lp(self) -> List[ActionToTake]:
        return [ActionToTake(
            entity_name='UNISWAP_V3',
            action=Action(action='deposit', args={'amount_in_notional': self._params.INITIAL_BALANCE})
        )]

    def _rebalance(self) -> List[ActionToTake]:
        actions = []
        entity: UniswapV3LPEntity = self.get_entity('UNISWAP_V3')

        # Step 1: Withdraw liquidity from the current range
        if entity.internal_state.positions:
            actions.append(
                ActionToTake(entity_name='UNISWAP_V3', action=Action(action='close_position', args={}))
            )
            self._debug("Liquidity withdrawn from the current range.")

        # Step 2: Calculate new range boundaries
        tau = self.tau
        reference_price: float = entity.global_state.price
        tick_spacing = self.tick_spacing
        price_lower = reference_price * 1.0001 ** (-tau * tick_spacing)
        price_upper = reference_price * 1.0001 ** (tau * tick_spacing)

        # Step 3: Open a new position centered around the new price
        delegate_get_cash = lambda obj: obj.get_entity('UNISWAP_V3').internal_state.cash
        actions.append(ActionToTake(
            entity_name='UNISWAP_V3',
            action=Action(
                action='open_position',
                args={
                    'amount_in_notional': delegate_get_cash,  # Allocate all available cash
                    'price_lower': price_lower,
                    'price_upper': price_upper
                }
            )
        ))
        self._debug(f"New position opened with range [{price_lower}, {price_upper}].")
        return actions
 