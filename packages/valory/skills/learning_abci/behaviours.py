# -*- coding: utf-8 -*-
# ------------------------------------------------------------------------------
#
#   Copyright 2024 Valory AG
#
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.
#
# ------------------------------------------------------------------------------

"""This package contains round behaviours of LearningAbciApp."""

from abc import ABC
from pathlib import Path
from tempfile import mkdtemp
from typing import Generator, Optional, Set, Type, cast

from packages.valory.skills.abstract_round_abci.base import AbstractRound
from packages.valory.skills.abstract_round_abci.behaviours import (
    AbstractRoundBehaviour,
    BaseBehaviour,
)
from packages.valory.skills.abstract_round_abci.io_.store import SupportedFiletype
from packages.valory.skills.learning_abci.models import (
    CoingeckoSpecs, 
    Params, SharedState
)
from packages.valory.skills.learning_abci.payloads import (
    APICheckPayload,
    IPFSPayload,
    DecisionMakingPayload,
    TxPreparationPayload,
)
from packages.valory.skills.learning_abci.rounds import (
    APICheckRound,
    DecisionMakingRound,
    Event,
    IPFSRound,
    LearningAbciApp,
    SynchronizedData,
    TxPreparationRound,
)


HTTP_OK = 200
GNOSIS_CHAIN_ID = "gnosis"
TX_DATA = b"0x"
SAFE_GAS = 0
VALUE_KEY = "value"
TO_ADDRESS_KEY = "to_address"
METADATA_FILENAME = "metadata.json"

class LearningBaseBehaviour(BaseBehaviour, ABC):  # pylint: disable=too-many-ancestors
    """Base behaviour for the learning_abci skill."""

    @property
    def synchronized_data(self) -> SynchronizedData:
        """Return the synchronized data."""
        return cast(SynchronizedData, super().synchronized_data)

    @property
    def params(self) -> Params:
        """Return the params."""
        return cast(Params, super().params)

    @property
    def local_state(self) -> SharedState:
        """Return the state."""
        return cast(SharedState, self.context.state)
    
    @property
    def coingecko_specs(self) -> CoingeckoSpecs:
        """Get the Coingecko api specs."""
        return self.context.coingecko_specs

    @property
    def metadata_filepath(self) -> str:
        """Get the temporary filepath to the metadata."""
        return str(Path(mkdtemp()) / METADATA_FILENAME)


class APICheckBehaviour(LearningBaseBehaviour):  # pylint: disable=too-many-ancestors
    """APICheckBehaviour"""

    matching_round: Type[AbstractRound] = APICheckRound

    def async_act(self) -> Generator:
        """Do the act, supporting asynchronous execution."""

        with self.context.benchmark_tool.measure(self.behaviour_id).local():
            sender = self.context.agent_address
            price = yield from self.get_price()
            ipfs_hash = yield from self.send_price_to_ipfs(price)
            balance = yield from self.get_balance()
            payload = APICheckPayload(
                sender=sender, 
                price=price, 
                balance=balance,
                cid=ipfs_hash
            )

        with self.context.benchmark_tool.measure(self.behaviour_id).consensus():
            yield from self.send_a2a_transaction(payload)
            yield from self.wait_until_round_end()

        self.set_done()

    def get_price(self):
        """Get token price from Coingecko"""
        # Interact with Coingecko's API
        # result = yield from self.get_http_response("coingecko.com")
        
        price = yield from self.get_token_price_specs()
        self.context.logger.info(f"Price is {price} ETH")
        return price

    def get_token_price_specs(self) -> Generator[None, None, Optional[float]] :
        """Get token price in ETH from Coingecko using ApiSpecs"""

        # Get the specs
        specs = self.coingecko_specs.get_spec()

        # Make the call
        raw_response = yield from self.get_http_response(**specs)

        # Process the response
        response = self.coingecko_specs.process_response(raw_response)

        # Get the price in ETH
        price = response.get("eth", None)
        self.context.logger.info(f"Got token price from Coingecko: {price} ETH")
        return price

    def get_balance(self):
        """Get balance"""
        # Use the contract api to interact with the ERC20 contract
        # result = yield from self.get_contract_api_response()
        yield
        balance = 1.0
        self.context.logger.info(f"Balance is {balance}")
        return balance

    def send_price_to_ipfs(self, price) -> Generator[None, None, Optional[str]]:
        """Store the token price in IPFS"""
        data = {"price": price}
        price_ipfs_hash = yield from self.send_to_ipfs(
            filename=self.metadata_filepath, obj=data, filetype=SupportedFiletype.JSON
        )
        self.context.logger.info(
            f"Price data stored in IPFS: https://gateway.autonolas.tech/ipfs/{price_ipfs_hash}"
        )
        return price_ipfs_hash


class IPFSBehaviour(LearningBaseBehaviour):
    """IPFSBehaviour: Retrieve the price from the IPFS"""

    matching_round : Type[AbstractRound] = IPFSRound

    def async_act(self) -> Generator:
        """Action"""
        
        sender = self.context.agent_address
        price = yield from self.get_price_from_ipfs()

        price = price.get('price')

        # MOCK logic
        if(cast(float, price) > 0):
            is_price_valid = True
        else:
            is_price_valid = False

        payload = IPFSPayload(
            sender=sender,
            is_price_valid=is_price_valid
        )

        yield from self.send_a2a_transaction(payload)
        yield from self.wait_until_round_end()
        
        self.set_done()

    def get_price_from_ipfs(self) -> Generator[None, None, Optional[dict]]:
        """Load the price data from IPFS"""
        ipfs_hash = self.synchronized_data.cid
        price = yield from self.get_from_ipfs(
            ipfs_hash=ipfs_hash, filetype=SupportedFiletype.JSON
        )
        self.context.logger.error(f"Got price from IPFS: {price}")
        return price

class DecisionMakingBehaviour(
    LearningBaseBehaviour
):  # pylint: disable=too-many-ancestors
    """DecisionMakingBehaviour"""

    matching_round: Type[AbstractRound] = DecisionMakingRound

    def async_act(self) -> Generator:
        """Do the act, supporting asynchronous execution."""

        with self.context.benchmark_tool.measure(self.behaviour_id).local():
            sender = self.context.agent_address
            event = self.get_event()
            payload = DecisionMakingPayload(sender=sender, event=event)

        with self.context.benchmark_tool.measure(self.behaviour_id).consensus():
            yield from self.send_a2a_transaction(payload)
            yield from self.wait_until_round_end()

        self.set_done()

    def get_event(self):
        """Get the next event"""
        # Using the token price from the previous round, decide whether we should make a transfer or not
        event = Event.DONE.value
        self.context.logger.info(f"Event is {event}")
        return event


class TxPreparationBehaviour(
    LearningBaseBehaviour
):  # pylint: disable=too-many-ancestors
    """TxPreparationBehaviour"""

    matching_round: Type[AbstractRound] = TxPreparationRound

    def async_act(self) -> Generator:
        """Do the act, supporting asynchronous execution."""

        with self.context.benchmark_tool.measure(self.behaviour_id).local():
            sender = self.context.agent_address
            tx_hash = yield from self.get_tx_hash()
            payload = TxPreparationPayload(
                sender=sender, tx_submitter=None, tx_hash=tx_hash
            )

        with self.context.benchmark_tool.measure(self.behaviour_id).consensus():
            yield from self.send_a2a_transaction(payload)
            yield from self.wait_until_round_end()

        self.set_done()

    def get_tx_hash(self):
        """Get the tx hash"""
        # We need to prepare a 1 wei transfer from the safe to another (configurable) account.
        yield
        tx_hash = None
        self.context.logger.info(f"Transaction hash is {tx_hash}")
        return tx_hash


class LearningRoundBehaviour(AbstractRoundBehaviour):
    """LearningRoundBehaviour"""

    initial_behaviour_cls = APICheckBehaviour
    abci_app_cls = LearningAbciApp  # type: ignore
    behaviours: Set[Type[BaseBehaviour]] = [  # type: ignore
        APICheckBehaviour,
        IPFSBehaviour,
        DecisionMakingBehaviour,
        TxPreparationBehaviour,
    ]
